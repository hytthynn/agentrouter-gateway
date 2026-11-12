"""Runtime-настройки: таблица `settings` (key-value), перекрывает дефолты. Hot-reload через подписки."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.core.logging import get_logger
from gateway_app.db.orm import Setting

log = get_logger(__name__)

# Дефолты §5.1 (+ параметры параллелизма FR-6.7 и авто-перелогина §11)
DEFAULTS: dict[str, str] = {
    "routing_strategy": "round_robin",  # round_robin | least_used | sequential
    "checkin_enabled": "true",
    "checkin_daily_at": "09:00",
    "checkin_relogin_after": "true",
    "balance_refresh_min": "15",
    "session_check_min": "30",
    "models_sync_min": "360",
    "default_client_profile": "codex",
    "log_retention_days": "30",
    "upstream_timeout_s": "600",
    "max_attempts": "3",
    "gateway_concurrency": "16",
    "account_concurrency": "4",
    "session_auto_relogin": "false",
    "provider_base_url": "https://agentrouter.org",
}

_INT_KEYS = {
    "balance_refresh_min",
    "session_check_min",
    "models_sync_min",
    "log_retention_days",
    "upstream_timeout_s",
    "max_attempts",
    "gateway_concurrency",
    "account_concurrency",
}
_BOOL_KEYS = {"checkin_enabled", "checkin_relogin_after", "session_auto_relogin"}
_CHOICE_KEYS = {"routing_strategy": {"round_robin", "least_used", "sequential"}}

_subscribers: list[Callable[[dict[str, str]], None]] = []


def subscribe_change(cb: Callable[[dict[str, str]], None]) -> None:
    """Подписка на изменения настроек (для hot-reload планировщика)."""
    _subscribers.append(cb)


def _notify(changed: dict[str, str]) -> None:
    if not changed:
        return
    for cb in _subscribers:
        try:
            cb(changed)
        except Exception:
            log.exception("Ошибка в подписчике настроек")


def validate(key: str, value: str) -> str:
    if key not in DEFAULTS:
        raise ValueError(f"Неизвестный ключ настройки: {key}")
    if key in _INT_KEYS:
        try:
            iv = int(value)
        except ValueError as exc:
            raise ValueError(f"{key}: ожидается целое число") from exc
        if (iv <= 0 and key != "log_retention_days") or (key == "log_retention_days" and iv < 1):
            raise ValueError(f"{key}: должно быть положительным")
        return str(iv)
    if key in _BOOL_KEYS:
        v = value.strip().lower()
        if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError(f"{key}: ожидается true/false")
        return "true" if v in ("true", "1", "yes", "on") else "false"
    if key in _CHOICE_KEYS:
        if value not in _CHOICE_KEYS[key]:
            raise ValueError(f"{key}: допустимо {'/'.join(sorted(_CHOICE_KEYS[key]))}")
        return value
    if key == "checkin_daily_at":
        hh, _, mm = value.partition(":")
        try:
            h, m = int(hh), int(mm)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError as exc:
            raise ValueError("checkin_daily_at: формат HH:MM") from exc
        return f"{h:02d}:{m:02d}"
    if key == "default_client_profile":
        return value.strip()
    if key == "provider_base_url":
        v = value.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("provider_base_url: ожидается URL с http(s)://")
        return v
    return value


async def get_all(session: AsyncSession) -> dict[str, str]:
    rows = (await session.exec(select(Setting))).all()
    stored = {r.key: r.value for r in rows}
    return {**DEFAULTS, **stored}


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row else DEFAULTS.get(key, "")


async def put_settings(session: AsyncSession, values: dict[str, str]) -> dict[str, str]:
    """Валидировать и сохранить; возвращает итоговое состояние. Уведомляет подписчиков."""
    changed: dict[str, str] = {}
    for key, raw in values.items():
        value = validate(key, str(raw))
        row = await session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
            session.add(row)
        elif row.value != value:
            row.value = value
        session.add(row)
        if DEFAULTS.get(key) != value:
            changed[key] = value
    await session.commit()
    _notify(changed)
    return await get_all(session)


@dataclass(frozen=True)
class RuntimeSettings:
    """Типизированный снапшот настроек (снимок на время операции)."""

    values: dict[str, str]

    @property
    def routing_strategy(self) -> str:
        return self.values["routing_strategy"]

    @property
    def checkin_enabled(self) -> bool:
        return self.values["checkin_enabled"] == "true"

    @property
    def checkin_daily_at(self) -> str:
        return self.values["checkin_daily_at"]

    @property
    def checkin_relogin_after(self) -> bool:
        return self.values["checkin_relogin_after"] == "true"

    @property
    def balance_refresh_min(self) -> int:
        return int(self.values["balance_refresh_min"])

    @property
    def session_check_min(self) -> int:
        return int(self.values["session_check_min"])

    @property
    def models_sync_min(self) -> int:
        return int(self.values["models_sync_min"])

    @property
    def default_client_profile(self) -> str:
        return self.values["default_client_profile"]

    @property
    def log_retention_days(self) -> int:
        return int(self.values["log_retention_days"])

    @property
    def upstream_timeout_s(self) -> int:
        return int(self.values["upstream_timeout_s"])

    @property
    def max_attempts(self) -> int:
        return int(self.values["max_attempts"])

    @property
    def gateway_concurrency(self) -> int:
        return int(self.values["gateway_concurrency"])

    @property
    def account_concurrency(self) -> int:
        return int(self.values["account_concurrency"])

    @property
    def session_auto_relogin(self) -> bool:
        return self.values["session_auto_relogin"] == "true"

    @property
    def provider_base_url(self) -> str:
        return self.values["provider_base_url"]


async def load(session: AsyncSession) -> RuntimeSettings:
    return RuntimeSettings(await get_all(session))


def defaults_json() -> dict[str, Any]:
    return dict(DEFAULTS)
