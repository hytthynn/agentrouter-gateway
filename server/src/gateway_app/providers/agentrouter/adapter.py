"""Адаптер AgentRouter: реализация протокола ProviderAdapter (§2.6 — единственная сетевая точка)."""

import json
from typing import Any, AsyncIterator

from gateway_app.core.logging import get_logger
from gateway_app.providers.agentrouter import client, errors, transport
from gateway_app.providers.base import (
    CheckinOutcome,
    CreateTokenSpec,
    PlatformStatus,
    ProviderCredentials,
    ProviderTokenInfo,
    UpstreamResponse,
    UpstreamStream,
    UserSelf,
)

log = get_logger(__name__)

QUOTA_PER_UNIT = 500_000  # §2.1: 500000 quota = $1


class AgentRouterAdapter:
    """Единственная реализация провайдера для MVP."""

    name = "agentrouter"

    # ---------------------------------------------------------- management
    async def get_user_self(self, creds: ProviderCredentials) -> UserSelf:
        return await client.get_user_self(creds)

    async def list_models(self, creds: ProviderCredentials) -> list[str]:
        return await client.list_models(creds)

    async def list_tokens(self, creds: ProviderCredentials) -> list[ProviderTokenInfo]:
        return await client.list_tokens(creds)

    async def create_token(
        self, creds: ProviderCredentials, spec: CreateTokenSpec
    ) -> ProviderTokenInfo:
        return await client.create_token(creds, spec)

    async def update_token(
        self, creds: ProviderCredentials, token_id: int, fields: dict[str, Any]
    ) -> ProviderTokenInfo:
        return await client.update_token(creds, token_id, fields)

    async def delete_token(self, creds: ProviderCredentials, token_id: int) -> None:
        await client.delete_token(creds, token_id)

    async def checkin(
        self, creds: ProviderCredentials, cached_path: str | None = None
    ) -> CheckinOutcome:
        return await client.checkin(creds, cached_path=cached_path)

    async def platform_status(self, base_url: str) -> PlatformStatus:
        return await client.platform_status(base_url)

    # ------------------------------------------------------------ upstream
    def _upstream_headers(
        self,
        api_key: str,
        *,
        profile_ua: str | None,
        profile_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if profile_ua:
            h["User-Agent"] = profile_ua
        if profile_headers:
            h.update(profile_headers)
        return h

    async def chat_non_stream(
        self,
        creds: ProviderCredentials,
        api_key: str,
        payload: dict[str, Any],
        *,
        profile_ua: str | None = None,
        profile_headers: dict[str, str] | None = None,
        timeout_s: int = 600,
    ) -> UpstreamResponse:
        url = creds.base_url.rstrip("/") + "/v1/chat/completions"
        headers = self._upstream_headers(api_key, profile_ua=profile_ua, profile_headers=profile_headers)
        status, body, text = await transport.request_json(
            "POST", url, headers=headers, json_body=payload, timeout_s=timeout_s, retries=0
        )
        if 200 <= status < 300 and body is not None:
            return UpstreamResponse(status=status, body=body)
        if body is None:
            raise errors.classify_upstream_error(status, None, text)
        raise errors.classify_upstream_error(status, body, text)

    def chat_stream(
        self,
        creds: ProviderCredentials,
        api_key: str,
        payload: dict[str, Any],
        *,
        profile_ua: str | None = None,
        profile_headers: dict[str, str] | None = None,
        timeout_s: int = 600,
    ) -> "AdapterStreamContext":
        url = creds.base_url.rstrip("/") + "/v1/chat/completions"
        headers = self._upstream_headers(api_key, profile_ua=profile_ua, profile_headers=profile_headers)
        return AdapterStreamContext(url=url, headers=headers, payload=payload, timeout_s=timeout_s)


class AdapterStreamContext:
    """Асинхронный контекст-менеджер стрима: открывает апстрим, проверяет статус/тело до выдачи."""

    def __init__(self, *, url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: int) -> None:
        self._url = url
        self._headers = headers
        self._payload = payload
        self._timeout = timeout_s
        self._wrapper: transport.StreamWrapper | None = None

    async def __aenter__(self) -> UpstreamStream:
        wrapper = await transport.open_stream(
            self._url, headers=self._headers, json_body=self._payload, timeout_s=self._timeout
        )
        self._wrapper = wrapper
        if 200 <= wrapper.status < 300:
            return _LiveStream(wrapper)

        # ошибка: прочитать тело, классифицировать, закрыть — failover ещё возможен
        chunks: list[bytes] = []
        try:
            async for chunk in wrapper.aiter_bytes():
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 65536:
                    break
        except Exception:  # noqa: BLE001
            pass
        await wrapper.aclose()
        text = b"".join(chunks).decode("utf-8", errors="replace")
        body: dict[str, Any] | None = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                body = parsed
        except json.JSONDecodeError:
            body = None
        raise errors.classify_upstream_error(wrapper.status, body, text)

    async def __aexit__(self, *exc: Any) -> None:
        if self._wrapper is not None:
            await self._wrapper.aclose()


class _LiveStream(UpstreamStream):
    """Живой SSE-поток апстрима с разбором usage из финального чанка."""

    def __init__(self, wrapper: transport.StreamWrapper) -> None:
        super().__init__(status=wrapper.status, headers=wrapper.headers, _close=wrapper.aclose)
        self._wrapper = wrapper
        self.last_usage: dict[str, Any] | None = None
        self.completion_chars = 0

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self._wrapper.aiter_bytes():
            self._track_usage(chunk)
            yield chunk

    def _track_usage(self, chunk: bytes) -> None:
        """Выделить usage из SSE-чанков и посчитать символы completion (§8.3)."""
        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                self.last_usage = obj["usage"]
            for choice in (obj.get("choices") or []) if isinstance(obj, dict) else []:
                if isinstance(choice, dict):
                    delta = choice.get("delta") or {}
                    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                        self.completion_chars += len(delta["content"])


def quota_to_usd(quota: int | None) -> float | None:
    return round(quota / QUOTA_PER_UNIT, 2) if quota is not None else None


__all__ = [
    "AgentRouterAdapter",
    "AdapterStreamContext",
    "quota_to_usd",
]
