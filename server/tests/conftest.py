"""Общие фикстуры: временная БД, тестовый SecretBox, FakeAdapter, httpx-клиент."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gateway_app.core.security import SecretBox
from gateway_app.db import database
from gateway_app.db import seed as db_seed
from gateway_app.db.orm import Account, AccountStatus, utcnow
from gateway_app.domain.accounts.base import creds_of
from gateway_app.providers.base import (
    CheckinOutcome,
    CreateTokenSpec,
    PlatformStatus,
    ProviderCredentials,
    ProviderTokenInfo,
    UpstreamResponse,
    UpstreamStream,
    UserSelf,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Свежая БД на каждый тест + тестовый мастер-ключ."""
    database.dispose_db()
    database.init_db(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    box = SecretBox(b"0" * 32)
    monkeypatch.setattr("gateway_app.core.security._box", box)
    yield
    database.dispose_db()


@pytest_asyncio.fixture
async def seeded_db() -> AsyncIterator[None]:
    async with database.make_session() as session:
        await database.create_all()
        await db_seed.seed(session)
    yield


class FakeAdapter:
    """Подменяет AgentRouterAdapter в тестах (§4: «адаптер мокается»)."""

    name = "fake"

    def __init__(self) -> None:
        self.user_quota = 12_500_000
        self.models = ["glm-4.6", "claude-opus-5-5"]
        self.checkin_result = CheckinOutcome(success=True, path="/api/user/checkin")
        self.chat_response: dict[str, Any] = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Привет!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.chat_failures: list[Exception] = []  # по попытке на вызов
        self.calls: list[dict[str, Any]] = []

    async def get_user_self(self, creds: ProviderCredentials) -> UserSelf:
        return UserSelf(
            id=101, username="tester", quota=self.user_quota, used_quota=1000,
            request_count=1, group="default", raw={},
        )

    async def list_models(self, creds: ProviderCredentials) -> list[str]:
        return list(self.models)

    async def list_tokens(self, creds: ProviderCredentials) -> list[ProviderTokenInfo]:
        return []

    async def create_token(
        self, creds: ProviderCredentials, spec: CreateTokenSpec
    ) -> ProviderTokenInfo:
        self.calls.append({"op": "create_token", "spec": spec})
        return ProviderTokenInfo(provider_token_id=1, name=spec.name, key_full="sk-fake-key-1234567890")

    async def update_token(
        self, creds: ProviderCredentials, token_id: int, fields: dict[str, Any]
    ) -> ProviderTokenInfo:
        self.calls.append({"op": "update_token", "token_id": token_id, "fields": fields})
        return ProviderTokenInfo(provider_token_id=token_id)

    async def delete_token(self, creds: ProviderCredentials, token_id: int) -> None:
        self.calls.append({"op": "delete_token", "token_id": token_id})

    async def checkin(self, creds: ProviderCredentials, cached_path=None) -> CheckinOutcome:
        self.calls.append({"op": "checkin", "path": cached_path})
        return self.checkin_result

    async def platform_status(self, base_url: str) -> PlatformStatus:
        return PlatformStatus(version="test-1.0", system_name="FakeRouter")

    async def chat_non_stream(
        self, creds: ProviderCredentials, api_key: str, payload: dict[str, Any], **kw: Any
    ) -> UpstreamResponse:
        self.calls.append({"op": "chat", "api_key": api_key, "model": payload.get("model")})
        if self.chat_failures:
            raise self.chat_failures.pop(0)
        return UpstreamResponse(status=200, body=self.chat_response)

    def chat_stream(self, creds: ProviderCredentials, api_key: str, payload: dict[str, Any], **kw: Any) -> Any:
        return FakeStreamContext(self.chat_response)


class FakeStreamContext:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    async def __aenter__(self) -> "FakeLiveStream":
        return FakeLiveStream(self.body)

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeLiveStream(UpstreamStream):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__(status=200, headers={}, _close=None)
        self.last_usage = body.get("usage")
        self.completion_chars = 10

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        chunk = {
            "id": "chatcmpl-1",
            "choices": [{"delta": {"content": "Привет!"}}],
            "usage": self.last_usage,
        }
        yield b"data: " + __import__("json").dumps(chunk).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"


@pytest_asyncio.fixture
async def fake_adapter() -> FakeAdapter:
    adapter = FakeAdapter()
    import gateway_app.api.deps as deps

    deps._adapter = adapter
    yield adapter
    deps._adapter = None


@pytest_asyncio.fixture
async def client(fake_adapter: FakeAdapter) -> AsyncIterator[AsyncClient]:
    from gateway_app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def account_with_key(fake_adapter: FakeAdapter) -> Account:
    """Аккаунт с безлимитным auto-ключом и моделями (через реальные сервисы)."""
    from gateway_app.domain.accounts.service import create_account_row
    from gateway_app.domain.catalog.models_sync import sync_account_models
    from gateway_app.domain.keys.provider_keys import create_auto_key

    async with database.make_session() as session:
        await database.create_all()
        box = __import__("gateway_app.core.security", fromlist=["get_secret_box"]).get_secret_box()
        acc = await create_account_row(
            session, box,
            name="Тест", base_url="https://fake.test",
            github_login="tester", github_password="pw", totp_secret="JBSWY3DPEHPK3PXP",
            system_token="sys-token", provider_user_id=101, username="tester",
            group_name="default", session_cookie=None, storage_state=None,
        )
        await create_auto_key(session, fake_adapter, box, acc)
        await sync_account_models(session, fake_adapter, box, acc)
        await session.refresh(acc)
        return acc


def make_account_direct(**kw: Any) -> dict[str, Any]:
    base = {
        "name": "Акк", "base_url": "https://fake.test", "github_login": "u",
        "provider_user_id": 101, "status": AccountStatus.active,
        "group_name": "default", "balance_quota": 12_500_000,
        "created_at": utcnow(),
    }
    base.update(kw)
    return base


__all__ = ["FakeAdapter", "creds_of", "make_account_direct"]
