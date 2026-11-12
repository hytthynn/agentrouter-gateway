"""Порт провайдера (чистая архитектура): домен зависит только от этого протокола."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProviderCredentials:
    """Расшифрованные креды аккаунта для вызовов провайдера."""

    base_url: str
    system_token: str
    provider_user_id: int | None = None


@dataclass
class UserSelf:
    """Профиль/баланс из GET /api/user/self."""

    id: int | None
    username: str | None
    quota: int | None
    used_quota: int | None
    request_count: int | None
    group: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ProviderTokenInfo:
    """Ключ из GET /api/token/ или ответа создания."""

    provider_token_id: int | None
    name: str | None = None
    key_full: str | None = None  # полный sk-... (только при создании/расшифровке)
    status: int | None = None  # 1 = активен (new-api)
    unlimited_quota: bool = True
    remain_quota: int | None = None
    used_quota: int | None = None
    expired_time: int = -1
    model_limits_enabled: bool = False
    model_limits: list[str] | None = None


@dataclass
class CreateTokenSpec:
    name: str
    unlimited_quota: bool = True
    remain_quota: int = 50_000_000
    expired_time: int = -1
    model_limits_enabled: bool = False
    model_limits: list[str] | None = None
    group: str = "default"


@dataclass
class CheckinOutcome:
    """Результат чек-ина (§2.3)."""

    success: bool
    already_done: bool = False
    message: str = ""
    path: str | None = None  # сработавший эндпоинт — кэшируется в настройках (§2.6)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class PlatformStatus:
    """Публичный GET /api/status."""

    version: str | None = None
    system_name: str | None = None
    quota_per_unit: int | None = None
    turnstile_check: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class UpstreamResponse:
    """Не-стрим ответ /v1/chat/completions апстрима."""

    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass
class UpstreamStream:
    """Открытый SSE-стрим апстрима. Вызывающий обязан закрыть."""

    status: int
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    _close: Any = None  # awaitable close()

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def close(self) -> None:
        if self._close is not None:
            await self._close()


@runtime_checkable
class ProviderAdapter(Protocol):
    """Абстракция провайдера: management API + upstream passthrough."""

    async def get_user_self(self, creds: ProviderCredentials) -> UserSelf: ...

    async def list_models(self, creds: ProviderCredentials) -> list[str]: ...

    async def list_tokens(self, creds: ProviderCredentials) -> list[ProviderTokenInfo]: ...

    async def create_token(
        self, creds: ProviderCredentials, spec: CreateTokenSpec
    ) -> ProviderTokenInfo: ...

    async def update_token(
        self,
        creds: ProviderCredentials,
        token_id: int,
        fields: dict[str, Any],
    ) -> ProviderTokenInfo: ...

    async def delete_token(self, creds: ProviderCredentials, token_id: int) -> None: ...

    async def checkin(self, creds: ProviderCredentials) -> CheckinOutcome: ...

    async def platform_status(self, base_url: str) -> PlatformStatus: ...

    async def chat_non_stream(
        self,
        creds: ProviderCredentials,
        api_key: str,
        payload: dict[str, Any],
        *,
        profile_name: str | None = None,
        profile_headers: dict[str, str] | None = None,
        profile_ua: str | None = None,
        timeout_s: int = 600,
    ) -> UpstreamResponse: ...

    def chat_stream(
        self,
        creds: ProviderCredentials,
        api_key: str,
        payload: dict[str, Any],
        *,
        profile_name: str | None = None,
        profile_headers: dict[str, str] | None = None,
        profile_ua: str | None = None,
        timeout_s: int = 600,
    ) -> Any:
        """Асинхронный контекст-менеджер → UpstreamStream."""
        ...
