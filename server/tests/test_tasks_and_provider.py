"""Тесты менеджера задач автоматизации (§9.3, §9.5) и провайдерского клиента (§2.6)."""

import asyncio

from gateway_app.automation.tasks import TaskState, manager
from gateway_app.providers.agentrouter.client import _looks_already_done, _norm_key, _token_from


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
    assert _looks_already_done("Already checked in today")
    assert not _looks_already_done("Internal error")
