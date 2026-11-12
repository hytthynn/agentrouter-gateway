"""Планировщик APScheduler (AsyncIOScheduler) + hot-reload расписаний из настроек."""

import asyncio
import contextlib
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gateway_app.core.logging import get_logger
from gateway_app.scheduler import jobs
from gateway_app.settings_store import subscribe_change

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> AsyncIOScheduler:
    """Создать и запустить планировщик со всеми джобами §11."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = AsyncIOScheduler(timezone=None)  # локальное время машины
    _scheduler = sched
    _schedule_all(sched)
    sched.start()
    subscribe_change(_on_settings_changed)
    log.info("Планировщик запущен: %d джоб", len(sched.get_jobs()))
    return sched


def _schedule_all(sched: AsyncIOScheduler) -> None:
    """AsyncIOScheduler умеет выполнять coroutine-функции напрямую — передаём их как есть."""

    async def _seed() -> None:
        await jobs.seed_router_state()

    sched.add_job(_seed, trigger="date", name="seed-routing-state")

    sched.add_job(
        jobs.job_checkin,
        CronTrigger(hour=9, minute=0),
        id="checkin",
        name="checkin",
        replace_existing=True,
    )
    sched.add_job(
        jobs.job_balance_refresh,
        IntervalTrigger(minutes=15),
        id="balance_refresh",
        name="balance_refresh",
        replace_existing=True,
    )
    sched.add_job(
        jobs.job_session_monitor,
        IntervalTrigger(minutes=30),
        id="session_monitor",
        name="session_monitor",
        replace_existing=True,
    )
    sched.add_job(
        jobs.job_models_sync,
        IntervalTrigger(minutes=360),
        id="models_sync",
        name="models_sync",
        replace_existing=True,
    )
    sched.add_job(
        jobs.job_retention,
        CronTrigger(hour=4, minute=0),
        id="retention",
        name="retention",
        replace_existing=True,
    )


async def reload_schedules(settings: dict[str, str]) -> None:
    """Hot-reload (FR-10.2): перечитать периоды/время чек-ина из settings."""
    if _scheduler is None:
        return

    def _minutes(key: str, default: int) -> int:
        try:
            return max(1, int(settings.get(key, default)))
        except (TypeError, ValueError):
            return default

    hh, mm = "09", "00"
    try:
        parts = settings.get("checkin_daily_at", "09:00").split(":")
        hh, mm = f"{int(parts[0]):02d}", f"{int(parts[1]):02d}"
    except (ValueError, IndexError):
        pass

    _scheduler.reschedule_job("checkin", trigger=CronTrigger(hour=hh, minute=mm))
    _scheduler.reschedule_job("balance_refresh", trigger=IntervalTrigger(minutes=_minutes("balance_refresh_min", 15)))
    _scheduler.reschedule_job("session_monitor", trigger=IntervalTrigger(minutes=_minutes("session_check_min", 30)))
    _scheduler.reschedule_job("models_sync", trigger=IntervalTrigger(minutes=_minutes("models_sync_min", 360)))
    log.info(
        "Расписания перечитаны: checkin=%s:%s, balance=%sм, session=%sм, sync=%sм",
        hh, mm, _minutes("balance_refresh_min", 15),
        _minutes("session_check_min", 30), _minutes("models_sync_min", 360),
    )


def _on_settings_changed(changed: dict[str, str]) -> None:
    relevant = {
        "checkin_daily_at", "balance_refresh_min", "session_check_min", "models_sync_min"
    }
    if relevant & changed.keys():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        _reload_tasks.add(asyncio.ensure_future(reload_schedules(changed)))


_reload_tasks: set[asyncio.Future[Any]] = set()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        with contextlib.suppress(Exception):
            _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_times() -> dict[str, Any]:
    """Для UI: ближайшие запуски джоб."""
    if _scheduler is None:
        return {}
    out: dict[str, Any] = {}
    for job in _scheduler.get_jobs():
        try:
            out[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
        except Exception:
            out[job.id] = None
    return out
