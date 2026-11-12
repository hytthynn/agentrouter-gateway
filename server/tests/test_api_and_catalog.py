"""Тесты каталога моделей (FR-4) и API аккаунтов/ключей (§7)."""

import asyncio

import pytest

from gateway_app.core.security import get_secret_box
from gateway_app.db import database
from gateway_app.db.orm import ModelCatalog
from gateway_app.domain.catalog.models_sync import catalog_view, sync_account_models

pytestmark = pytest.mark.usefixtures("seeded_db")


async def test_sync_union_and_availability(seeded_db, fake_adapter, account_with_key):
    box = get_secret_box()
    async with database.make_session() as session:
        await sync_account_models(session, fake_adapter, box, account_with_key)
        models = (await session.exec(__import__("sqlmodel", fromlist=["select"]).select(ModelCatalog))).all()
        ids = {m.model_id for m in models}
        assert ids == {"glm-4.6", "claude-opus-5-5"}

        view = await catalog_view(session)
        assert all(v["accounts_available"] == 1 for v in view)


async def test_model_disabled_hidden(client, fake_adapter, account_with_key):
    key_resp = await client.post("/api/keys", json={"name": "k"})
    key = key_resp.json()["data"]["full_key"]
    # выключаем модель
    await client.patch("/api/models/glm-4.6", json={"enabled": False})
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    ids = [m["id"] for m in resp.json()["data"]]
    assert "glm-4.6" not in ids and "claude-opus-5-5" in ids
    # 404 при запросе с hint
    resp2 = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": []},
    )
    assert resp2.status_code == 404
    assert "выключена" in resp2.json()["error"]["message"]


async def test_accounts_crud_and_provider_keys(client, fake_adapter, account_with_key):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    accounts = resp.json()["data"]
    assert len(accounts) == 1
    assert accounts[0]["name"] == "Тест"

    # ключи провайдера: auto-ключ создан
    keys = (await client.get("/api/provider-keys")).json()["data"]
    assert len(keys) == 1
    assert keys[0]["created_via"] == "auto"
    assert keys[0]["unlimited_quota"] is True
    assert keys[0]["key_masked"].startswith("sk-")

    # reveal
    reveal = await client.get(f"/api/provider-keys/{keys[0]['id']}/reveal")
    assert reveal.json()["data"]["key"] == "sk-fake-key-1234567890"

    # toggle
    toggle = await client.patch(f"/api/provider-keys/{keys[0]['id']}", json={"enabled": False})
    assert toggle.json()["data"]["status"] == "disabled"

    # удалить аккаунт с purge
    delete = await client.delete(f"/api/accounts/{account_with_key.id}?purge_provider_keys=true")
    assert delete.status_code == 200
    assert delete.json()["data"]["provider_keys_deleted"] == 1
    assert fake_adapter.calls[-1]["op"] == "delete_token"

    # аккаунтов больше нет
    accounts = (await client.get("/api/accounts")).json()["data"]
    assert accounts == []


async def test_add_account_requires_fields(client):
    resp = await client.post("/api/accounts", json={"github_login": "x"})
    assert resp.status_code == 422


async def test_dashboard_aggregates(client, fake_adapter, account_with_key):
    key_resp = await client.post("/api/keys", json={"name": "k"})
    key = key_resp.json()["data"]["full_key"]
    await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [{"role": "user", "content": "hello world test"}]},
    )
    await asyncio.sleep(0.15)
    resp = await client.get("/api/dashboard")
    data = resp.json()["data"]
    assert data["accounts_total"] == 1
    assert data["requests_24h"] == 1
    assert data["tokens_24h"] == 15
    assert len(data["sparkline"]) == 25


async def test_health_endpoint(client):
    resp = await client.get("/api/health")
    data = resp.json()["data"]
    assert data["db"] == "ok"
    assert data["encryption"] == "ok"


async def test_logs_filters(client, fake_adapter, account_with_key):
    key_resp = await client.post("/api/keys", json={"name": "k"})
    key = key_resp.json()["data"]["full_key"]
    await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": []},
    )
    await asyncio.sleep(0.15)
    resp = await client.get("/api/logs/requests", params={"model": "glm"})
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["model"] == "glm-4.6"
    assert items[0]["status"] == "success"


async def test_retention_deletes_old(client, seeded_db):
    from datetime import timedelta

    from gateway_app.db.orm import RequestLog, RequestStatus, utcnow
    from gateway_app.domain.logs.service import cleanup_old_logs

    async with database.make_session() as session:
        session.add(RequestLog(model="m", ts=utcnow() - timedelta(days=40), status=RequestStatus.success))
        session.add(RequestLog(model="m", ts=utcnow(), status=RequestStatus.success))
        await session.commit()
        result = await cleanup_old_logs(session, request_days=30)
        assert result["request_logs"] == 1
