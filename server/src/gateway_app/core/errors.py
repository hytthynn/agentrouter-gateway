"""Типизированные ошибки приложения."""

from typing import Any


class AppError(Exception):
    """Базовая ошибка: код + сообщение + HTTP-статус."""

    code = "app_error"
    http_status = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ValidationError(AppError):
    code = "validation_error"
    http_status = 422


class ConflictError(AppError):
    code = "conflict"
    http_status = 409


class ProviderError(AppError):
    """Ошибка взаимодействия с провайдером (транспорт/HTTP/формат)."""

    code = "provider_error"
    http_status = 502


class ProviderAuthError(ProviderError):
    """401 от консольного API — токен отозван/сессия просрочена (→ needs_relogin)."""

    code = "provider_auth_error"
    http_status = 401


class WafBlockedError(ProviderError):
    """Aliyun WAF отверг клиента («Unauthorized client detected»/HTML-блок)."""

    code = "waf_blocked"
    http_status = 403


class BudgetPoolExhaustedError(ProviderError):
    """402 `Budget pool quota has been exhausted` — дневной пул модели исчерпан."""

    code = "budget_pool_exhausted"
    http_status = 402

    def __init__(self, message: str, *, next_refill_utc: str | None = None) -> None:
        super().__init__(message)
        self.next_refill_utc = next_refill_utc


class NoAvailableChannelError(ProviderError):
    """503 `no available channel`."""

    code = "no_available_channel"
    http_status = 503


class RateLimitedError(ProviderError):
    """429 от апстрима."""

    code = "rate_limited"
    http_status = 429


class AutomationError(AppError):
    """Ошибка браузерной автоматизации (GitHub OAuth)."""

    code = "automation_error"
    http_status = 502


class AutomationUserCodeRequired(AutomationError):
    """GitHub запрашивает device-verification код из письма — нужен ввод пользователя (§9.5)."""

    code = "automation_user_code_required"
    http_status = 409
