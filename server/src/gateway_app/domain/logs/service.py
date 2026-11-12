"""Системные логи и логи запросов: запись, фильтры, пагинация (FR-7)."""

from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import or_, select

from gateway_app.core.masking import mask_text, truncate
from gateway_app.db import orm
from gateway_app.db.orm import utcnow

Order = Literal["asc", "desc"]


async def log_system(
    session: AsyncSession,
    level: str,
    category: str,
    message: str,
    *,
    account_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """FR-7.2: запись системного лога (секреты маскируются)."""
    try:
        row = orm.SystemLog(
            level=orm.SystemLogLevel(level),
            category=orm.SystemLogCategory(category),
            account_id=account_id,
            message=mask_text(truncate(message, 2000)),
            details=details,
        )
        session.add(row)
        await session.commit()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Не удалось записать system log")


async def query_system_logs(
    session: AsyncSession,
    *,
    level: str | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    stmt = select(orm.SystemLog)
    if level:
        stmt = stmt.where(orm.SystemLog.level == orm.SystemLogLevel(level))
    if category:
        stmt = stmt.where(orm.SystemLog.category == orm.SystemLogCategory(category))
    if account_id:
        stmt = stmt.where(orm.SystemLog.account_id == account_id)
    if search:
        stmt = stmt.where(orm.SystemLog.message.contains(search))
    total = (await session.exec(select(func.count()).select_from(stmt.subquery()))).one()
    rows = (
        await session.exec(
            stmt.order_by(orm.SystemLog.ts.desc(), orm.SystemLog.id.desc())  # type: ignore[attr-defined]
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {"items": [system_log_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def system_log_out(r: orm.SystemLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts,
        "level": r.level.value if isinstance(r.level, orm.SystemLogLevel) else r.level,
        "category": r.category.value if isinstance(r.category, orm.SystemLogCategory) else r.category,
        "account_id": r.account_id,
        "message": r.message,
        "details": r.details,
    }


async def log_request(session: AsyncSession, row: orm.RequestLog) -> None:
    session.add(row)
    await session.commit()


async def query_request_logs(
    session: AsyncSession,
    *,
    status: str | None = None,
    gateway_key_id: int | None = None,
    account_id: int | None = None,
    model: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    stmt = select(orm.RequestLog)
    if status:
        stmt = stmt.where(orm.RequestLog.status == orm.RequestStatus(status))
    if gateway_key_id:
        stmt = stmt.where(orm.RequestLog.gateway_key_id == gateway_key_id)
    if account_id:
        stmt = stmt.where(orm.RequestLog.account_id == account_id)
    if model:
        stmt = stmt.where(orm.RequestLog.model.contains(model))
    if since:
        stmt = stmt.where(orm.RequestLog.ts >= since)
    if until:
        stmt = stmt.where(orm.RequestLog.ts <= until)
    if search:
        stmt = stmt.where(
            or_(orm.RequestLog.error_message.contains(search), orm.RequestLog.model.contains(search))
        )
    total = (await session.exec(select(func.count()).select_from(stmt.subquery()))).one()
    rows = (
        await session.exec(
            stmt.order_by(orm.RequestLog.ts.desc(), orm.RequestLog.id.desc())  # type: ignore[attr-defined]
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {"items": [request_log_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def request_log_out(r: orm.RequestLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts,
        "gateway_key_id": r.gateway_key_id,
        "account_id": r.account_id,
        "model": r.model,
        "stream": r.stream,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
        "estimated": r.estimated,
        "cost_quota": r.cost_quota,
        "cost_usd": r.cost_usd,
        "duration_ms": r.duration_ms,
        "first_token_ms": r.first_token_ms,
        "status": r.status.value if isinstance(r.status, orm.RequestStatus) else r.status,
        "http_status": r.http_status,
        "error_code": r.error_code,
        "error_message": r.error_message,
        "attempts": r.attempts,
        "client_ua": r.client_ua,
    }


async def cleanup_old_logs(session: AsyncSession, *, request_days: int) -> dict[str, int]:
    """Ретеншн (FR-7.3): request_logs ×N, system_logs ×3 (§5.1)."""
    now = utcnow()
    r = (
        await session.exec(
            select(orm.RequestLog).where(  # type: ignore[attr-defined]
                orm.RequestLog.ts < now - timedelta(days=request_days)
            )
        )
    ).all()
    s = (
        await session.exec(
            select(orm.SystemLog).where(  # type: ignore[attr-defined]
                orm.SystemLog.ts < now - timedelta(days=request_days * 3)
            )
        )
    ).all()
    for row in r:
        await session.delete(row)
    for row in s:
        await session.delete(row)
    await session.commit()
    return {"request_logs": len(r), "system_logs": len(s)}
