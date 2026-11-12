"""Транспорт к agentrouter.org: curl_cffi с TLS-имперсонацией Chrome (§10.1).

Единственная точка выхода в сеть провайдера. Singleton AsyncSession, keep-alive.
"""

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any

from curl_cffi.requests import AsyncSession

from gateway_app.core.errors import ProviderError
from gateway_app.core.logging import get_logger

log = get_logger(__name__)

IMPERSONATE = "chrome"  # актуальная версия отпечатка Chrome
CONNECT_TIMEOUT_S = 15

_session: AsyncSession | None = None
_lock = asyncio.Lock()


def get_session() -> AsyncSession:
    global _session
    if _session is None:
        _session = AsyncSession(impersonate=IMPERSONATE, verify=True)
    return _session


async def close_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None


def _timeout_tuple(total_s: float) -> tuple[int, int]:
    return (CONNECT_TIMEOUT_S, max(1, int(total_s)))


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
    retries: int = 2,
) -> tuple[int, dict[str, Any] | None, str]:
    """JSON-запрос с ограниченными ретраями и экспоненциальным backoff + джиттером.

    Возвращает (status, json|None, text). Поднимает ProviderError на сетевых сбоях.
    """
    session = get_session()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=_timeout_tuple(timeout_s),
            )
            text = resp.text if resp.text is not None else ""
            body: dict[str, Any] | None = None
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" in ct or text[:1] in ("{", "["):
                try:
                    parsed = resp.json()
                    body = parsed if isinstance(parsed, dict) else None
                except Exception:  # noqa: BLE001 — тело не JSON
                    body = None
            return resp.status_code, body, text
        except Exception as exc:  # curl_cffi: Errors / Timeout — сетевой уровень
            last_exc = exc
            if attempt < retries:
                delay = min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.4)
                log.debug("Транспорт: ретрай %s %s через %.1fs (%s)", method, url, delay, exc)
                await asyncio.sleep(delay)
    raise ProviderError(f"Транспорт: {method} {url} не удался: {last_exc}")


def looks_like_waf(status: int, text: str, content_type: str = "") -> bool:
    """Эвристика WAF-блока: HTML-страница вместо JSON, маркеры Aliyun."""
    if status in (403, 405) and "json" not in content_type:
        return True
    low = text[:2000].lower()
    return any(
        marker in low
        for marker in ("aliyun", "captcha", "unauthorized client", "request blocked", "waf")
    )


async def stream_post(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any],
    timeout_s: float = 600,
) -> AsyncIterator[Any]:
    """Открыть стрим POST. Уступает объект ответа с aiter_content/aclose (контекст-менеджер)."""
    session = get_session()
    resp = await session.post(
        url,
        headers=headers,
        json=json_body,
        stream=True,
        timeout=_timeout_tuple(timeout_s),
    )
    return resp


class StreamWrapper:
    """Обёртка стрим-ответа: aiter_content + aclose; проверка статуса до чтения."""

    def __init__(self, resp: Any) -> None:
        self._resp = resp

    @property
    def status(self) -> int:
        return int(self._resp.status_code)

    @property
    def headers(self) -> dict[str, str]:
        try:
            return {str(k): str(v) for k, v in self._resp.headers.items()}
        except Exception:  # noqa: BLE001
            return {}

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").lower()

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self._resp.aiter_content():
            if chunk:
                yield chunk

    async def text(self) -> str:
        try:
            return self._resp.text or ""
        except Exception:  # noqa: BLE001
            return ""

    async def aclose(self) -> None:
        try:
            await self._resp.aclose()
        except Exception:  # noqa: BLE001
            pass


async def open_stream(
    url: str, *, headers: dict[str, str], json_body: dict[str, Any], timeout_s: float = 600
) -> StreamWrapper:
    """Открыть SSE-стрим; статус проверяется вызывающим (нужна классификация тела)."""
    resp = await stream_post(url, headers=headers, json_body=json_body, timeout_s=timeout_s)
    return StreamWrapper(resp)


__all__ = [
    "get_session",
    "close_session",
    "request_json",
    "open_stream",
    "StreamWrapper",
    "looks_like_waf",
]
