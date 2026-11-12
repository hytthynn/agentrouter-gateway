"""Тесты runtime-настроек (§5.1 settings, hot-reload)."""

import pytest

from gateway_app import settings_store
from gateway_app.settings_store import DEFAULTS, RuntimeSettings, put_settings, validate


async def test_defaults_used_when_empty(seeded_db):
    async with database_session() as session:
        all_settings = await settings_store.get_all(session)
        assert all_settings["routing_strategy"] == "round_robin"
        assert all_settings["checkin_daily_at"] == "09:00"
        assert all_settings["max_attempts"] == "3"


def database_session():
    from gateway_app.db.database import make_session

    return make_session()


def test_validate_choices():
    assert validate("routing_strategy", "least_used") == "least_used"
    with pytest.raises(ValueError):
        validate("routing_strategy", "random")


def test_validate_time():
    assert validate("checkin_daily_at", "9:5") == "09:05"
    with pytest.raises(ValueError):
        validate("checkin_daily_at", "25:00")


def test_validate_bool_and_int():
    assert validate("checkin_enabled", "yes") == "true"
    assert validate("checkin_enabled", "0") == "false"
    with pytest.raises(ValueError):
        validate("max_attempts", "abc")
    with pytest.raises(ValueError):
        validate("unknown_key", "x")


async def test_put_settings_and_notification(seeded_db):
    changes: list[dict] = []
    settings_store.subscribe_change(changes.append)
    async with database_session() as session:
        result = await put_settings(session, {"routing_strategy": "sequential", "checkin_daily_at": "10:30"})
        assert result["routing_strategy"] == "sequential"
    assert "routing_strategy" in changes[0]


async def test_runtime_settings_snapshot(seeded_db):
    async with database_session() as session:
        rs = RuntimeSettings(await settings_store.get_all(session))
        assert rs.routing_strategy == "round_robin"
        assert rs.checkin_enabled is True
        assert rs.max_attempts == 3
        assert rs.provider_base_url == DEFAULTS["provider_base_url"]
