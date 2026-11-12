"""Тесты ключей шлюза (FR-5): лимиты, аутентификация, истечение."""

from datetime import timedelta

from gateway_app.core.security import get_secret_box
from gateway_app.db import database
from gateway_app.db.orm import KeyStatus, utcnow
from gateway_app.domain.keys.gateway_keys import (
    authenticate_key,
    check_key_usable,
    create_gateway_key,
    reveal_gateway_key,
)


async def test_create_and_authenticate(seeded_db):
    box = get_secret_box()
    async with database.make_session() as session:
        row, full = await create_gateway_key(
            session, box, name="k", token_limit=100, expires_at=None, allowed_models=None
        )
        assert full.startswith("agw-")
        assert row.key_masked != full

        found, reason = await authenticate_key(session, box, full)
        assert found is not None and reason is None
        assert found.id == row.id

        missing, reason = await authenticate_key(session, box, "agw-unknown")
        assert missing is None and reason == "invalid"

        none_key, reason = await authenticate_key(session, box, "")
        assert none_key is None and reason == "missing"


async def test_key_limits(seeded_db):
    box = get_secret_box()
    async with database.make_session() as session:
        row, _full = await create_gateway_key(
            session, box, name="k", token_limit=1000, expires_at=None, allowed_models=["glm-4.6"]
        )
        assert check_key_usable(row) is None
        assert check_key_usable(row, model="glm-4.6") is None
        assert check_key_usable(row, model="other-model") == "model_not_allowed"

        row.tokens_used = 1000
        assert check_key_usable(row) == "limit"

        row.tokens_used = 0
        row.status = KeyStatus.disabled
        assert check_key_usable(row) == "disabled"

        row.status = KeyStatus.active
        row.expires_at = utcnow() - timedelta(hours=1)
        assert check_key_usable(row) == "expired"


async def test_reveal(seeded_db):
    box = get_secret_box()
    async with database.make_session() as session:
        row, full = await create_gateway_key(
            session, box, name="k", token_limit=None, expires_at=None, allowed_models=None
        )
        assert await reveal_gateway_key(session, box, row.id) == full
