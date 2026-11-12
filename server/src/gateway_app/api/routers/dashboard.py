"""Роутер дашборда (FR-9): агрегаты, спарклайн за 24 ч, статусы, лента логов."""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from gateway_app.api.deps import SessionDep
from gateway_app.db import orm
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.base import account_out, list_accounts
from gateway_app.domain.logs.service import query_system_logs

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(session: SessionDep) -> dict[str, Any]:
    now = utcnow()
    since = now - timedelta(hours=24)

    accounts = await list_accounts(session)
    active = [a for a in accounts if a.status is orm.AccountStatus.active]

    # агрегаты запросов за 24 ч
    counts = (
        await session.exec(
            select(
                orm.RequestLog.status,
                func.count(orm.RequestLog.id),
                func.coalesce(func.sum(orm.RequestLog.total_tokens), 0),
            )
            .where(orm.RequestLog.ts >= since)  # type: ignore[attr-defined]
            .group_by(orm.RequestLog.status)
        )
    ).all()
    by_status = {s.value if hasattr(s, "value") else s: (c, t) for s, c, t in counts}
    req_total = sum(c for c, _ in by_status.values())
    tokens_24h = sum(int(t) for _, t in by_status.values())
    errors_24h = by_status.get("error", (0, 0))[0] + by_status.get("client_abort", (0, 0))[0]

    # спарклайн по часам (запросы + токены)
    rows = (
        await session.exec(
            select(orm.RequestLog.ts, orm.RequestLog.total_tokens)
            .where(orm.RequestLog.ts >= since)  # type: ignore[attr-defined]
        )
    ).all()
    buckets: list[dict[str, Any]] = []
    hour0 = since.replace(minute=0, second=0, microsecond=0)
    for i in range(25):
        h = hour0 + timedelta(hours=i)
        buckets.append({"hour": h.isoformat() + "Z", "requests": 0, "tokens": 0})
    for ts, tokens in rows:
        idx = int((ts - hour0).total_seconds() // 3600)
        if 0 <= idx < len(buckets):
            buckets[idx]["requests"] += 1
            buckets[idx]["tokens"] += int(tokens or 0)

    # ближайший чек-ин (из настроек; реальное время даёт планировщик — app.state)
    from gateway_app.settings_store import get_setting

    checkin_at = await get_setting(session, "checkin_daily_at")
    next_checkin = _next_checkin_iso(checkin_at)

    recent_logs = await query_system_logs(session, page=1, page_size=10)

    total_balance_usd = round(sum(a.balance_usd or 0.0 for a in accounts), 2)
    total_quota = sum(a.balance_quota or 0 for a in accounts)

    return {
        "success": True,
        "data": {
            "total_balance_usd": total_balance_usd,
            "total_balance_quota": total_quota,
            "accounts_active": len(active),
            "accounts_total": len(accounts),
            "requests_24h": req_total,
            "tokens_24h": tokens_24h,
            "errors_24h": errors_24h,
            "next_checkin": next_checkin,
            "sparkline": buckets,
            "accounts": [account_out(a) for a in accounts],
            "recent_logs": recent_logs["items"],
        },
    }


def _next_checkin_iso(hhmm: str) -> str:
    """Ближайшее время HH:MM локальное → ISO (информация; точность даёт APScheduler)."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return hhmm
    now = datetime.now()
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt = nxt + timedelta(days=1)
    return nxt.isoformat(timespec="minutes")
