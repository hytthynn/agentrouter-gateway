"""Первичная инициализация: сид настроек и client-профилей (§10.2)."""

from sqlalchemy.ext.asyncio import AsyncSession

from gateway_app.core.logging import get_logger
from gateway_app.db import orm

log = get_logger(__name__)

# Точные UA-строки калибруются через диагностику (§8.6) и правятся в настройках.
SEED_PROFILES: list[dict] = [
    {
        "name": "codex",
        "user_agent": (
            "codex_cli_rs/0.42.0 (Ubuntu 22.04.5 LTS; x86_64) WindowsTerminal"
        ),
        "extra_headers": {"originator": "codex_cli_rs"},
        "is_default": True,
    },
    {
        "name": "claude-code",
        "user_agent": "claude-cli/2.0.14 (external, cli)",
        "extra_headers": {
            "x-app": "cli",
            "anthropic-version": "2023-06-01",
        },
        "is_default": False,
    },
    {
        "name": "zcode",
        "user_agent": "zcode/1.0.0 (client zcode)",
        "extra_headers": {},
        "is_default": False,
    },
    {
        "name": "kilo-code",
        "user_agent": "kilo-code/4.0 (VSCode; kilo)",
        "extra_headers": {},
        "is_default": False,
    },
    {
        "name": "cline",
        "user_agent": "cline/3.10 (VSCode; cline)",
        "extra_headers": {},
        "is_default": False,
    },
    {
        "name": "browser",
        # SPA-профиль для консольных (management) запросов — §10.2
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "extra_headers": {
            "accept": "application/json, text/plain, */*",
            "origin": "https://agentrouter.org",
        },
        "is_default": False,
    },
]


async def seed(session: AsyncSession) -> None:
    """Сид профилей имперсонации — только отсутствующих строк (настройки живут в settings_store)."""
    for spec in SEED_PROFILES:
        name = spec["name"]
        if await session.get(orm.ClientProfile, name) is None:
            session.add(orm.ClientProfile(**spec))
            log.info("Сид профиля имперсонации: %s", name)
    await session.commit()
