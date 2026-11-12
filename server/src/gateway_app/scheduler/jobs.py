"""Джобы планировщика (§11): checkin, balance_refresh, session_monitor, models_sync, retention."""

import asyncio
import contextlib
from typing import Any

from gateway_app.api.deps import get_adapter
from gateway_app.automation.tasks import AutomationTask
from gateway_app.core.errors import ProviderAuthError
from gateway_app.core.logging import get_logger
from gateway_app.core.security import get_secret_box
from gateway_app.db import orm
from gateway_app.db.database import make_session
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.service import (
    creds_of,
    list_accounts,
    refresh_balance,
    relogin_flow,
)
from gateway_app.domain.catalog.models_sync import sync_catalog
from gateway_app.domain.checkin.service import run_checkin_all
from gateway_app.domain.logs.service import cleanup_old_logs, log_system
from gateway_app.settings_store import load

log = get_logger(__name__)

JOB_NAMES = {"checkin", "balance_refresh", "session_monitor", "models_sync", "retention"}

_running: set[str] = set()


def _busy(name: str) -> bool:
    return name in _running


async def _guarded(name: str, body) -> Any:
    """Джоба не запускается повторно, пока прошлый запуск активен (§11)."""
    if _busy(name):
        log.info("Джоба %s уже выполняется — пропуск", name)
        return {"skipped": "already_running"}
    _running.add(name)
    session = make_session()
    try:
        await log_system(session, "info", "scheduler", f"Джоба {name} запущена")
        result = await body(session)
        await log_system(session, "info", "scheduler", f"Джоба {name} завершена: {result}")
        return result
    except Exception as exc:
        log.exception("Джоба %s упала", name)
        with contextlib.suppress(Exception):
            await log_system(session, "error", "scheduler", f"Джоба {name} упала: {exc}")
        return {"error": str(exc)}
    finally:
        _running.discard(name)
        await session.close()


async def job_checkin() -> Any:
    """FR-8.2: чек-ин всех активных, параллелизм 2, случайный порядок, джиттер 0–15 мин."""

    async def body(session) -> Any:
        settings = await load(session)
        if not settings.checkin_enabled:
            return {"skipped": "checkin_disabled"}
        adapter = get_adapter()
        box = get_secret_box()
        results = await run_checkin_all(
            session, adapter, box, relogin_after=settings.checkin_relogin_after,
            parallel=2, jitter_s=15 * 60,
        )
        summary = {}
        for r in results:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        return {"accounts": len(results), "summary": summary}

    return await _guarded("checkin", body)


async def job_balance_refresh() -> Any:
    async def body(session) -> Any:
        adapter = get_adapter()
        ok = await _refresh_accounts(session, adapter)
        return {"accounts_refreshed": ok}

    return await _guarded("balance_refresh", body)


async def _refresh_accounts(session, adapter) -> int:
    box = get_secret_box()
    accounts = await list_accounts(session)
    ok = 0
    for acc in accounts:
        if acc.status not in (orm.AccountStatus.active, orm.AccountStatus.needs_relogin):
            continue
        try:
            await refresh_balance(session, adapter, box, acc)
            ok += 1
        except Exception as exc:
            await log_system(session, "error", "balance", f"{acc.name}: {exc}", account_id=acc.id)
    return ok


async def job_session_monitor() -> Any:
    """§11: проверка сессий/токенов; needs_relogin при 401; авто-перелогин если включён."""

    async def body(session) -> Any:
        settings = await load(session)
        adapter = get_adapter()
        box = get_secret_box()
        accounts = await list_accounts(session)
        bad = 0
        relogin_done = 0
        for acc in accounts:
            if acc.status is not orm.AccountStatus.active:
                continue
            try:
                await adapter.get_user_self(creds_of(box, acc))
            except ProviderAuthError as exc:
                bad += 1
                acc.status = orm.AccountStatus.needs_relogin
                acc.error_message = str(exc)[:300]
                acc.updated_at = utcnow()
                session.add(acc)
                await session.commit()
                await log_system(
                    session, "warn", "oauth", f"{acc.name}: сессия/токен недействительны → needs_relogin",
                    account_id=acc.id,
                )
                if settings.session_auto_relogin and acc.session_enc:
                    task = AutomationTask(kind="auto_relogin", account_id=acc.id)
                    try:
                        await relogin_flow(task, make_session, adapter, acc.id)  # type: ignore[arg-type]
                        relogin_done += 1
                    except Exception as exc2:
                        await log_system(
                            session, "error", "oauth", f"{acc.name}: авто-перелогин не удался: {exc2}",
                            account_id=acc.id,
                        )
        return {"checked": len(accounts), "needs_relogin": bad, "auto_relogin_done": relogin_done}

    return await _guarded("session_monitor", body)


async def job_models_sync() -> Any:
    async def body(session) -> Any:
        adapter = get_adapter()
        return await sync_catalog(session, adapter, get_secret_box())

    return await _guarded("models_sync", body)


async def job_retention() -> Any:
    async def body(session) -> Any:
        settings = await load(session)
        return await cleanup_old_logs(session, request_days=settings.log_retention_days)

    return await _guarded("retention", body)


async def trigger_job(name: str) -> Any:
    """Ручной триггер через API (§11): POST /api/tasks/{job}/run."""
    fns = {
        "checkin": job_checkin,
        "balance_refresh": job_balance_refresh,
        "session_monitor": job_session_monitor,
        "models_sync": job_models_sync,
        "retention": job_retention,
    }
    return await fns[name]()


async def seed_router_state() -> None:
    """Инициализация счётчиков least_used токенами из БД (после рестарта)."""
    from sqlalchemy import func
    from sqlmodel import select

    from gateway_app.domain.routing.strategy import router_state

    session = make_session()
    try:
        rows = (
            await session.exec(
                select(orm.RequestLog.account_id, func.coalesce(func.sum(orm.RequestLog.total_tokens), 0))
                .group_by(orm.RequestLog.account_id)  # type: ignore[attr-defined]
            )
        ).all()
        for account_id, tokens in rows:
            if account_id is not None:
                router_state.seed_tokens(account_id, int(tokens))
    finally:
        await session.close()


_ = asyncio  # keep import for wrappers
