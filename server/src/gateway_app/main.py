"""FastAPI-приложение: lifespan, роутеры, статика web/dist, обработка ошибок."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from gateway_app import __version__
from gateway_app.core.config import get_config
from gateway_app.core.errors import AppError
from gateway_app.core.logging import get_logger, setup_logging
from gateway_app.db import database
from gateway_app.db import seed as db_seed

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация: логи → БД (+авто-миграции) → seed → планировщик. Graceful shutdown."""
    cfg = get_config()
    setup_logging(cfg.log_level, cfg.app_log_file)
    log.info("AgentRouter Gateway %s — запуск (data: %s)", __version__, cfg.data_dir)

    database.init_db(cfg.db_url_async)
    await ensure_migrated(cfg)

    from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: F401

    async with database.make_session() as session:
        await db_seed.seed(session)

    from gateway_app.scheduler.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        from gateway_app.providers.agentrouter import transport

        await transport.close_session()
        await database.dispose_db()
        log.info("Завершение работы: планировщик остановлен, ресурсы освобождены")


async def ensure_migrated(cfg: Any) -> None:
    """Если таблиц нет — применить миграции автоматически (удобство первого запуска)."""
    from sqlalchemy import text

    engine = database.engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'")
        )
        has_tables = result.first() is not None
    if has_tables:
        return
    log.info("Схема БД отсутствует — применяю Alembic-миграции…")
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", cfg.db_url_sync)
    command.upgrade(alembic_cfg, "head")
    log.info("Миграции применены")


def create_app() -> FastAPI:
    app = FastAPI(title="AgentRouter Gateway", version=__version__, lifespan=lifespan)

    # Локальная зона доверия (§13.1)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1", "http://localhost",
            "http://127.0.0.1:8787", "http://localhost:8787",
            "http://127.0.0.1:5173", "http://localhost:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Роутеры
    from gateway_app.api.routers import (
        accounts,
        dashboard,
        health,
        keys,
        logs,
        models,
        provider_keys,
        settings,
    )
    from gateway_app.gateway.router import router as gateway_router

    app.include_router(accounts.router)
    app.include_router(accounts.router_tasks)
    app.include_router(provider_keys.router)
    app.include_router(models.router)
    app.include_router(keys.router)
    app.include_router(logs.router)
    app.include_router(dashboard.router)
    app.include_router(settings.router)
    app.include_router(settings.router_profiles)
    app.include_router(health.router)
    app.include_router(gateway_router)

    @app.get("/api/version")
    async def version() -> dict[str, Any]:
        return {"success": True, "data": {"version": __version__}}

    # Обработка доменных ошибок: {"success": false, "error": {...}}
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"success": False, "error": exc.to_dict()},
        )

    # Статика web/dist с SPA-fallback (монтируется последней)
    cfg = get_config()
    dist = cfg.default_web_dist()
    if dist.exists() and (dist / "index.html").exists():
        app.mount(
            "/",
            SPAStaticFiles(directory=dist, html=True),
            name="web",
        )
        log.info("Статика UI: %s", dist)
    else:
        log.warning("web/dist не найден — UI недоступен (соберите: cd web && pnpm build)")

    return app


class SPAStaticFiles(StaticFiles):
    """StaticFiles с fallback на index.html для клиентских маршрутов React."""

    async def get_response(self, path: str, scope: Any) -> Any:
        try:
            response = await super().get_response(path, scope)
        except Exception:
            response = None
        if response is not None and getattr(response, "status_code", 500) != 404:
            return response
        # не-API путь → index.html
        if path.startswith(("api/", "v1/")):
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": {"code": "not_found", "message": "Не найдено"}},
            )
        return await super().get_response("index.html", scope)


app = create_app()
