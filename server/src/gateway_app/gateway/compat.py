"""Ошибки шлюза в OpenAI-формате (FR-6.8, §8.1): {"error": {"type", "code", "message"}}."""

from typing import Any

from fastapi.responses import JSONResponse

from gateway_app.core.errors import (
    AppError,
    BudgetPoolExhaustedError,
    NoAvailableChannelError,
    ProviderAuthError,
    WafBlockedError,
)

REFILL_HINT = "Пополнения дневных пулов: 02:00 и 11:00 UTC"


def error_payload(err_type: str, code: str, message: str) -> dict[str, Any]:
    return {"error": {"type": err_type, "code": code, "message": message}}


def error_response(
    status: int, err_type: str, code: str, message: str
) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_payload(err_type, code, message))


def auth_error(message: str) -> JSONResponse:
    return error_response(401, "authentication_error", "invalid_api_key", message)


def not_found_error(message: str, code: str = "model_not_found") -> JSONResponse:
    return error_response(404, "invalid_request_error", code, message)


def quota_error(message: str) -> JSONResponse:
    return error_response(429, "insufficient_quota", "insufficient_quota", message)


def bad_request_error(message: str) -> JSONResponse:
    return error_response(400, "invalid_request_error", "invalid_request", message)


def provider_error_response(exc: Exception, *, attempts_left: list[dict[str, Any]] | None = None) -> JSONResponse:
    """Ошибка апстрима → OpenAI-формат с подсказками (FR-6.5)."""
    if isinstance(exc, BudgetPoolExhaustedError):
        hint = f"{exc.message}. {REFILL_HINT}."
        if exc.next_refill_utc:
            hint += f" Ближайшее пополнение: {exc.next_refill_utc}."
        if attempts_left:
            names = ", ".join(str(a.get("account_id")) for a in attempts_left)
            hint += f" Остались аккаунты: {names}."
        return error_response(402, "insufficient_quota", "budget_pool_exhausted", hint)
    if isinstance(exc, NoAvailableChannelError):
        return error_response(503, "api_error", "no_available_channel", f"{exc.message}. Попробуйте другую модель или позже.")
    if isinstance(exc, ProviderAuthError):
        return error_response(401, "authentication_error", "upstream_auth", exc.message)
    if isinstance(exc, WafBlockedError):
        return error_response(403, "api_error", "waf_blocked", f"{exc.message}. Смените client-профиль в настройках.")
    if isinstance(exc, AppError):
        return error_response(exc.http_status, "api_error", exc.code, exc.message)
    return error_response(502, "api_error", "upstream_error", str(exc))
