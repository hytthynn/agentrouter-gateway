"""Тесты менеджера задач автоматизации (§9.3, §9.5) и провайдерского клиента (§2.6)."""

import asyncio

from gateway_app.automation.tasks import TaskState, manager
from gateway_app.providers.agentrouter.client import _looks_already_done, _norm_key, _token_from
from gateway_app.providers.base import CheckinOutcome


class FakeAdapterForUnavailable:
    """Адаптер, у которого все кандидаты чек-ина — 404 (форк без фичи)."""

    async def checkin(self, creds, cached_path=None):
        return CheckinOutcome(False, False, "Чек-ин недоступен у провайдера", unavailable=True)

    async def get_user_self(self, creds):
        from gateway_app.providers.base import UserSelf

        return UserSelf(id=1, username="u", quota=100, used_quota=0, request_count=0, group=None)


async def test_checkin_unavailable_is_info_not_error(seeded_db):
    from gateway_app.core.security import get_secret_box
    from gateway_app.core.security import get_secret_box as _box
    from gateway_app.db import database
    from gateway_app.db.orm import Account, AccountStatus, utcnow
    from gateway_app.domain.checkin.service import run_checkin
    from gateway_app.domain.logs.service import query_system_logs

    _b = _box()
    acc = Account(
        name="acc", base_url="https://x", github_login="u",
        github_password_enc=_b.encrypt("github_password_enc", "pw"),
        totp_secret_enc=_b.encrypt("totp_secret_enc", "JBSWY3DPEHPK3PXP"),
        system_token_enc=_b.encrypt("system_token_enc", "sys-token"),
        status=AccountStatus.active, created_at=utcnow(),
    )
    async with database.make_session() as session:
        session.add(acc)
        await session.commit()
        await session.refresh(acc)
        result = await run_checkin(
            session, FakeAdapterForUnavailable(), get_secret_box(), acc, relogin_after=False
        )
        assert result["status"] == "unavailable"
        logs = await query_system_logs(session, category="checkin", page=1, page_size=5)
        assert logs["items"], "info-лог должен быть записан"
        assert logs["items"][0]["level"] == "info"


async def test_task_lifecycle():
    task = manager.create("add_account")
    assert task.state is TaskState.running

    async def body(t):
        t.add_step("oauth", "GitHub OAuth")
        t.log("шаг")
        t.step_status("oauth", "done")
        return {"ok": 1}

    await manager.run(task, body)
    assert task.state is TaskState.success
    assert task.result == {"ok": 1}
    assert task.steps[0].status == "done"


async def test_task_error_state():
    task = manager.create("relogin")

    async def body(t):
        raise RuntimeError("boom")

    await manager.run(task, body)
    assert task.state is TaskState.error
    assert "boom" in task.error


async def test_wait_user_code_flow():
    task = manager.create("test")

    async def body(t):
        code = await t.wait_user_code(timeout_s=5)
        return {"code": code}

    async def provider():
        await asyncio.sleep(0.05)
        assert task.provide_user_code("123456")

    await asyncio.gather(manager.run(task, body), provider())
    assert task.result == {"code": "123456"}


def test_cleanup_removes_old():
    task = manager.create("old")
    task.finish_success({})
    task.created_at = task.created_at.replace(year=2000)
    removed = manager.cleanup(max_age_s=3600)
    assert removed >= 1


# ------------------------------------------------- провайдерский клиент

def test_norm_key_adds_sk_prefix():
    assert _norm_key("abc") == "sk-abc"
    assert _norm_key("sk-abc") == "sk-abc"
    assert _norm_key(None) is None


def test_token_from_parses_full_object():
    info = _token_from(
        {
            "id": 5,
            "name": "k",
            "key": "abc123",
            "unlimited_quota": True,
            "expired_time": -1,
            "model_limits_enabled": False,
            "model_limits": "",
            "status": 1,
            "used_quota": 42,
        }
    )
    assert info.provider_token_id == 5
    assert info.key_full == "sk-abc123"
    assert info.unlimited_quota is True
    assert info.expired_time == -1
    assert info.used_quota == 42


def test_already_done_detection():
    assert _looks_already_done("Вы уже чекинились сегодня")


async def test_create_token_fallback_via_list(seeded_db, monkeypatch):
    """Форк AgentRouter: POST /api/token/ не возвращает key → добираем из списка (§2.6)."""
    from gateway_app.core.security import get_secret_box
    from gateway_app.providers.agentrouter import client as ar_client
    from gateway_app.providers.base import CreateTokenSpec, ProviderCredentials

    creds = ProviderCredentials(base_url="https://fake.test", system_token="t", provider_user_id=1)

    async def fake_call(creds_, method, path, *, json_body=None, timeout_s=30.0):
        if method == "POST":
            return {"success": True, "message": "", "data": ""}  # без key — как реальный форк
        assert path.startswith("/api/token/?p=")
        return {
            "success": True,
            "data": {"page": 1, "total": 1, "items": [
                {"id": 77, "name": "agw-auto-unlimited", "key": "rawkey123", "unlimited_quota": True,
                 "expired_time": -1, "status": 1},
            ]},
        }

    monkeypatch.setattr(ar_client, "_call", fake_call)
    get_secret_box()
    info = await ar_client.create_token(creds, CreateTokenSpec(name="agw-auto-unlimited"))
    assert info.provider_token_id == 77
    assert info.key_full == "sk-rawkey123"


async def test_list_models_group_dict_format(seeded_db, monkeypatch):
    """Форк: GET /api/models возвращает словарь «группа → список» → union."""
    from gateway_app.providers.agentrouter import client as ar_client
    from gateway_app.providers.base import ProviderCredentials

    creds = ProviderCredentials(base_url="https://fake.test", system_token="t", provider_user_id=1)

    async def fake_call(creds_, method, path, *, json_body=None, timeout_s=30.0):
        if path == "/api/user/models":
            return {"success": True, "data": ["glm-4.6"]}
        if path == "/api/models":
            return {"success": True, "data": {"1": ["gpt-4o", "glm-4.6"], "2": ["claude-opus"]}}
        raise AssertionError(path)

    monkeypatch.setattr(ar_client, "_call", fake_call)
    models = await ar_client.list_models(creds)
    # primary /api/user/models (доступные аккаунту)
    assert models == ["glm-4.6"]

    async def empty_user_models(creds_, method, path, *, json_body=None, timeout_s=30.0):
        if path == "/api/user/models":
            return {"success": True, "data": []}
        return {"success": True, "data": {"1": ["gpt-4o", "glm-4.6"], "2": ["claude-opus"]}}

    monkeypatch.setattr(ar_client, "_call", empty_user_models)
    models = await ar_client.list_models(creds)
    assert models == ["gpt-4o", "glm-4.6", "claude-opus"]  # union групп, без дублей
    assert _looks_already_done("Already checked in today")
    assert not _looks_already_done("Internal error")
