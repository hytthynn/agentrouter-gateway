"""Асинхронный движок SQLite (WAL) + фабрика сессий."""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine.Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """WAL + FK + busy_timeout — на каждое соединение."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def init_db(url: str, echo: bool = False) -> AsyncEngine:
    """Инициализировать глобальный движок (idempotent)."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    _engine = create_async_engine(url, echo=echo, pool_pre_ping=False)
    event.listen(_engine.sync_engine, "connect", _set_sqlite_pragma)
    _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def dispose_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.sync_engine.dispose()
    _engine = None
    _sessionmaker = None


def engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("БД не инициализирована: вызовите init_db()")
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: сессия на запрос."""
    if _sessionmaker is None:
        raise RuntimeError("БД не инициализирована: вызовите init_db()")
    async with _sessionmaker() as session:
        yield session


def make_session() -> AsyncSession:
    """Сессия для фоновых задач (вызывающий закрывает сам / контекстный менеджер)."""
    if _sessionmaker is None:
        raise RuntimeError("БД не инициализирована: вызовите init_db()")
    return _sessionmaker()


async def create_all() -> None:
    """Создать таблицы (для тестов; в prod — Alembic миграции)."""
    eng = engine()
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
