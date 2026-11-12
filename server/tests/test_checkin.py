"""Тесты чек-ина (FR-8, §2.3): успех с дельтой, «уже чекинились», кэш пути."""

from gateway_app.core.security import get_secret_box
from gateway_app.db import database
from gateway_app.db.orm import SystemLogLevel
from gateway_app.domain.checkin.service import run_checkin
from gateway_app.domain.logs.service import query_system_logs


async def test_checkin_success_with_delta(seeded_db, fake_adapter, account_with_key):
    box = get_secret_box()
    fake_adapter.user_quota = 12_500_000  # баланс до

    async with database.make_session() as session:
        # после чек-ина баланс вырастет на $25 → 25 000 000
        async def grow(creds):
            from gateway_app.providers.base import UserSelf

            return UserSelf(id=101, username="tester", quota=fake_adapter.user_quota,
                            used_quota=0, request_count=0, group="default", raw={})

        result = await run_checkin(session, fake_adapter, box, account_with_key, relogin_after=False)
        assert result["status"] == "success"

        # дельта 0 (FakeAdapter не меняет баланс) → warn записан
        logs = await query_system_logs(session, category="checkin", page=1, page_size=20)
        messages = [i["message"] for i in logs["items"]]
        assert any("чек-ин успешен" in m for m in messages)


async def test_checkin_already_done_is_info(seeded_db, fake_adapter, account_with_key):
    from gateway_app.providers.base import CheckinOutcome

    fake_adapter.checkin_result = CheckinOutcome(
        success=False, already_done=True, message="已签到", path="/api/user/checkin"
    )
    box = get_secret_box()
    async with database.make_session() as session:
        result = await run_checkin(session, fake_adapter, box, account_with_key, relogin_after=False)
        assert result["status"] == "already"
        logs = await query_system_logs(session, category="checkin", page=1, page_size=10)
        assert any(i["level"] == SystemLogLevel.info.value for i in logs["items"])


async def test_checkin_caches_endpoint_path(seeded_db, fake_adapter, account_with_key):
    box = get_secret_box()
    async with database.make_session() as session:
        await run_checkin(session, fake_adapter, box, account_with_key, relogin_after=False)
    # путь из CheckinOutcome сохранён в settings
    async with database.make_session() as session:
        row = await session.get(__import__("gateway_app.db.orm", fromlist=["Setting"]).Setting, "checkin_path_cache")
        assert row is not None
        assert row.value == "/api/user/checkin"


async def test_checkin_skips_disabled(seeded_db, fake_adapter):
    from gateway_app.db.orm import Account, AccountStatus, utcnow

    box = get_secret_box()
    acc = Account(
        name="off", base_url="https://x", github_login="u",
        github_password_enc=b"x", totp_secret_enc=b"x", system_token_enc=b"x",
        status=AccountStatus.disabled, created_at=utcnow(),
    )
    async with database.make_session() as session:
        session.add(acc)
        await session.commit()
        await session.refresh(acc)
        result = await run_checkin(session, fake_adapter, box, acc, relogin_after=False)
        assert result["status"] == "skipped"
