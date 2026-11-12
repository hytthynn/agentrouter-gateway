"""Alembic environment: URL из конфига приложения, метаданные SQLModel."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from gateway_app.core.config import get_config
from gateway_app.db import orm  # noqa: F401 — регистрация моделей
from sqlmodel import SQLModel

config = context.config

# URL из pydantic-config (env AGW_* / дефолты) — синхронный sqlite:/// для миграций
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_config().db_url_sync)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite: ALTER TABLE через batch
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
