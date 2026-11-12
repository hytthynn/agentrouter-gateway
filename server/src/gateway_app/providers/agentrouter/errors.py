"""Классификация ошибок апстрима AgentRouter (§2.5)."""

from typing import Any

from gateway_app.core.errors import (
    BudgetPoolExhaustedError,
    NoAvailableChannelError,
    ProviderAuthError,
    ProviderError,
    RateLimitedError,
    WafBlockedError,
)

# маркеры в теле ответа → тип ошибки
_WAF_MARKERS = ("unauthorized client detected", "waf", "blocked", "captcha", "cloudfront", "aliyun")
_BUDGET_MARKERS = ("budget pool quota has been exhausted", "budget pool")
_CHANNEL_MARKERS = ("no available channel",)
_TOKEN_MARKERS = ("invalid token", "无效的令牌", "令牌无效")

REFILL_HOURS_UTC = (2, 11)  # пополнения пулов 02:00 и 11:00 UTC


def next_refill_iso_utc() -> str:
    """Ближайшее время пополнения пула (02:00/11:00 UTC) — ISO-строка."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    candidates = []
    for day_offset in (0, 1):
        for hour in REFILL_HOURS_UTC:
            t = (now + timedelta(days=day_offset)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            if t > now:
                candidates.append(t)
    return min(candidates).isoformat()


def classify_upstream_error(
    status: int, body: dict[str, Any] | None, text: str = ""
) -> ProviderError:
    """HTTP-статус + тело апстрима → типизированная ошибка домена."""
    message = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
        if not message:
            message = str(body.get("message") or "")
    if not message and text:
        message = text[:300]

    low = message.lower()
    if any(m in low for m in _BUDGET_MARKERS) or status == 402:
        return BudgetPoolExhaustedError(
            "Дневной пул квоты модели исчерпан у провайдера",
            next_refill_utc=next_refill_iso_utc(),
        )
    if any(m in low for m in _WAF_MARKERS):
        return WafBlockedError(f"WAF отклонил клиент: {message or status}")
    if any(m in low for m in _CHANNEL_MARKERS) or status == 503:
        return NoAvailableChannelError(f"Нет доступных каналов: {message or status}")
    if status == 401:
        if any(m in low for m in _TOKEN_MARKERS):
            return ProviderAuthError(f"Ключ провайдера отклонён: {message or 'Invalid token'}")
        return WafBlockedError(f"Клиент не распознан WAF: {message or status}")
    if status == 429:
        return RateLimitedError(f"Лимит апстрима: {message or status}")
    return ProviderError(f"Ошибка апстрима HTTP {status}: {message or 'без тела'}")


def is_retriable(exc: Exception) -> bool:
    """Failover-набор кодов (FR-6.4): 401/402/403/429/500/502/503 + WAF."""
    if isinstance(exc, (BudgetPoolExhaustedError, NoAvailableChannelError, RateLimitedError)):
        return True
    if isinstance(exc, WafBlockedError):
        return True
    if isinstance(exc, ProviderAuthError):
        return True  # 401 → другой аккаунт
    return False
