"""Per-request accounting (FR-6.6, §8.3): запись request_logs + инкремент счётчиков ключа шлюза."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway_app.db import orm
from gateway_app.db.orm import utcnow
from gateway_app.domain.routing.strategy import router_state


def estimate_tokens(messages: list[dict[str, Any]] | None, completion_chars: int) -> tuple[int, int]:
    """Оценка токенов: ~символы/4 (FR-6.2). Возвращает (prompt_tokens, completion_tokens)."""
    prompt_chars = 0
    for m in messages or []:
        if isinstance(m, dict):
            prompt_chars += len(str(m.get("content") or ""))
    return max(1, prompt_chars // 4), max(1, completion_chars // 4) if completion_chars else 0


async def record_usage(
    session: AsyncSession,
    *,
    gateway_key_id: int | None,
    account_id: int | None,
    model: str,
    stream: bool,
    prompt_tokens: int,
    completion_tokens: int,
    estimated: bool,
    cost_quota: int | None = None,
    duration_ms: int,
    first_token_ms: int | None = None,
    status: orm.RequestStatus = orm.RequestStatus.success,
    http_status: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
    client_ua: str | None = None,
) -> orm.RequestLog:
    total = prompt_tokens + completion_tokens
    row = orm.RequestLog(
        gateway_key_id=gateway_key_id,
        account_id=account_id,
        model=model,
        stream=stream,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        estimated=estimated,
        cost_quota=cost_quota,
        cost_usd=round(cost_quota / 500_000, 6) if cost_quota is not None else None,
        duration_ms=duration_ms,
        first_token_ms=first_token_ms,
        status=status,
        http_status=http_status,
        error_code=error_code,
        error_message=error_message[:2048] if error_message else None,
        attempts=attempts,
        client_ua=client_ua[:300] if client_ua else None,
    )
    session.add(row)
    if gateway_key_id is not None:
        key = await session.get(orm.GatewayKey, gateway_key_id)
        if key is not None:
            key.tokens_used += total
            key.request_count += 1
            key.last_used_at = utcnow()
            session.add(key)
    if account_id is not None and total > 0:
        router_state.add_usage(account_id, total)
    await session.commit()
    return row
