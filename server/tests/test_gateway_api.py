"""Интеграционные тесты шлюза (FR-6): auth, 404 модели, accounting, failover 402, SSE."""


import pytest

from gateway_app.core.security import get_secret_box
from gateway_app.db import database
from gateway_app.db.orm import RequestStatus
from gateway_app.domain.keys.gateway_keys import create_gateway_key
from gateway_app.providers.agentrouter import errors as ar_errors
from gateway_app.providers.agentrouter.errors import BudgetPoolExhaustedError

pytestmark = pytest.mark.usefixtures("seeded_db")


async def _key() -> str:
    box = get_secret_box()
    async with database.make_session() as session:
        _, full = await create_gateway_key(
            session, box, name="t", token_limit=None, expires_at=None, allowed_models=None
        )
    return full


async def test_models_requires_auth(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["type"] == "authentication_error"


async def test_models_list_openai_format(client, account_with_key):
    key = await _key()
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "glm-4.6" in ids and "claude-opus-5-5" in ids
    assert all(m["owned_by"] == "agentrouter" for m in data["data"])


async def test_model_404_with_hint(client):
    key = await _key()
    resp = await client.get("/v1/models/unknown-model", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert "отсутствует" in resp.json()["error"]["message"]


async def test_chat_non_stream_accounting(client, fake_adapter, account_with_key):
    key = await _key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [{"role": "user", "content": "привет"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "Привет!"
    assert body["usage"]["total_tokens"] == 15

    # accounting
    from sqlmodel import select

    async with database.make_session() as session:
        logs = (await session.exec(select(__import__("gateway_app.db.orm", fromlist=["RequestLog"]).RequestLog))).all()
        assert len(logs) == 1
        lg = logs[0]
        assert lg.status is RequestStatus.success
        assert lg.total_tokens == 15
        assert lg.account_id == account_with_key.id
        assert lg.model == "glm-4.6"
        # счётчик ключа шлюза
        from gateway_app.db.orm import GatewayKey

        gk = await session.get(GatewayKey, lg.gateway_key_id)
        assert gk is not None and gk.tokens_used == 15 and gk.request_count == 1


async def test_chat_failover_on_budget_pool(client, fake_adapter, account_with_key):
    """402 от первого аккаунта → попытка на следующем; trace в attempts (FR-6.4/6.5)."""
    # второй аккаунт с той же моделью и ключом
    from gateway_app.domain.accounts.service import create_account_row
    from gateway_app.domain.catalog.models_sync import sync_account_models

    # sequential делает порядок кандидатов детерминированным (acc1 → acc2)
    await client.put("/api/settings", json={"values": {"routing_strategy": "sequential"}})

    box = get_secret_box()
    async with database.make_session() as session:
        acc2 = await create_account_row(
            session, box, name="Запасной", base_url="https://fake.test",
            github_login="tester2", github_password="pw", totp_secret="JBSWY3DPEHPK3PXP",
            system_token="sys2", provider_user_id=102, username="tester2",
            group_name="default", session_cookie=None, storage_state=None,
        )
        await sync_account_models(session, fake_adapter, box, acc2)
        from gateway_app.domain.keys.provider_keys import create_auto_key

        await create_auto_key(session, fake_adapter, box, acc2)

    fake_adapter.chat_failures = [BudgetPoolExhaustedError("Budget pool quota has been exhausted")]

    key = await _key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200  # failover на второй аккаунт успешен
    assert fake_adapter.calls[-1]["op"] == "chat"

    from sqlmodel import select

    async with database.make_session() as session:
        logs = (await session.exec(select(__import__("gateway_app.db.orm", fromlist=["RequestLog"]).RequestLog))).all()
        assert len(logs) == 1
        lg = logs[0]
        assert lg.account_id == acc2.id


async def test_budget_pool_error_shows_refill_hint(client, fake_adapter, account_with_key):
    """Только один аккаунт + 402 → типизированная ошибка с подсказкой (FR-6.5)."""
    fake_adapter.chat_failures = [BudgetPoolExhaustedError("Budget pool quota has been exhausted")]
    key = await _key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": []},
    )
    assert resp.status_code == 402
    err = resp.json()["error"]
    assert err["code"] == "budget_pool_exhausted"
    assert "02:00 и 11:00 UTC" in err["message"]


async def test_chat_stream_accounting(client, fake_adapter, account_with_key):
    key = await _key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [], "stream": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"data: " in resp.content

    from sqlmodel import select

    async with database.make_session() as session:
        logs = (await session.exec(select(__import__("gateway_app.db.orm", fromlist=["RequestLog"]).RequestLog))).all()
        assert len(logs) == 1
        lg = logs[0]
        assert lg.stream is True
        assert lg.status is RequestStatus.success
        assert lg.total_tokens == 15  # usage из стрима
        assert lg.first_token_ms is not None


async def test_unknown_model_not_routed(client, fake_adapter, account_with_key):
    key = await _key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "no-such", "messages": []},
    )
    assert resp.status_code == 404


async def test_token_limit_returns_429(client, fake_adapter, account_with_key):
    box = get_secret_box()
    async with database.make_session() as session:
        _, full = await create_gateway_key(
            session, box, name="limited", token_limit=10, expires_at=None, allowed_models=None
        )
        from sqlmodel import select

        from gateway_app.db.orm import GatewayKey

        gk = (await session.exec(select(GatewayKey).where(GatewayKey.name == "limited"))).one()
        gk.tokens_used = 10  # лимит исчерпан (FR-5.3)
        session.add(gk)
        await session.commit()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {full}"},
        json={"model": "glm-4.6", "messages": []},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "insufficient_quota"


def test_classify_waf():
    exc = ar_errors.classify_upstream_error(401, {"error": {"message": "Unauthorized client detected"}})
    assert isinstance(exc, ar_errors.WafBlockedError)
    assert ar_errors.is_retriable(exc)
