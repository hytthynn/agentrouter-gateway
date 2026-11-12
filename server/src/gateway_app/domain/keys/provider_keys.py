"""Ключи провайдера (upstream sk-...): создание, toggle, удаление, синк расхода (FR-2)."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from gateway_app.core.errors import NotFoundError, ProviderAuthError
from gateway_app.core.logging import get_logger
from gateway_app.core.security import SecretBox, mask_secret
from gateway_app.db import orm
from gateway_app.domain.accounts.base import creds_of, get_account
from gateway_app.domain.logs.service import log_system
from gateway_app.providers.base import (
    CreateTokenSpec,
    ProviderAdapter,
    ProviderCredentials,
)

log = get_logger(__name__)


def provider_key_out(k: orm.ProviderKey, account_name: str = "") -> dict[str, Any]:
    return {
        "id": k.id,
        "account_id": k.account_id,
        "account_name": account_name,
        "name": k.name,
        "key_masked": k.key_masked,
        "provider_token_id": k.provider_token_id,
        "unlimited_quota": k.unlimited_quota,
        "remain_quota": k.remain_quota,
        "expired_time": k.expired_time,
        "model_limits_enabled": k.model_limits_enabled,
        "model_limits": k.model_limits,
        "status": k.status.value if isinstance(k.status, orm.KeyStatus) else k.status,
        "created_via": k.created_via.value if isinstance(k.created_via, orm.KeyOrigin) else k.created_via,
        "used_quota": k.used_quota,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
    }


async def get_provider_key(session: AsyncSession, key_id: int) -> orm.ProviderKey:
    row = await session.get(orm.ProviderKey, key_id)
    if row is None:
        raise NotFoundError(f"Ключ провайдера {key_id} не найден")
    return row


async def list_keys(
    session: AsyncSession, account_id: int | None = None
) -> list[dict[str, Any]]:
    """Список ключей (опц. по аккаунту) с именем аккаунта для UI (FR-2.3)."""
    stmt = select(orm.ProviderKey).order_by(orm.ProviderKey.account_id, orm.ProviderKey.created_at.desc())  # type: ignore[attr-defined]
    if account_id:
        stmt = stmt.where(orm.ProviderKey.account_id == account_id)  # type: ignore[attr-defined]
    rows = (await session.exec(stmt)).all()
    names: dict[int, str] = {}
    if rows:
        accs = (await session.exec(select(orm.Account))).all()
        names = {a.id: a.name for a in accs}
    return [provider_key_out(r, names.get(r.account_id, "")) for r in rows]


async def create_auto_key(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, account: orm.Account
) -> orm.ProviderKey:
    """FR-2.1: безлимитный, бессрочный ключ со всеми моделями, name='agw-auto-unlimited'."""
    creds = creds_of(box, account)
    info = await adapter.create_token(
        creds,
        CreateTokenSpec(name="agw-auto-unlimited", unlimited_quota=True, expired_time=-1),
    )
    full = info.key_full or ""
    row = orm.ProviderKey(
        account_id=account.id,  # type: ignore[arg-type]
        name=info.name or "agw-auto-unlimited",
        key_enc=box.encrypt("key_enc", full),
        key_masked=mask_secret(full),
        provider_token_id=info.provider_token_id,
        unlimited_quota=True,
        remain_quota=None,
        expired_time=-1,
        model_limits_enabled=False,
        model_limits=None,
        status=orm.KeyStatus.active,
        created_via=orm.KeyOrigin.auto,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await log_system(
        session, "info", "sync", f"Авто-ключ {row.key_masked} выпущен для {account.name}",
        account_id=account.id,
    )
    return row


async def create_manual_key(
    session: AsyncSession,
    adapter: ProviderAdapter,
    box: SecretBox,
    account_id: int,
    *,
    name: str,
    unlimited_quota: bool,
    remain_quota: int | None,
    expired_time: int,
    model_limits_enabled: bool,
    model_limits: list[str] | None,
) -> orm.ProviderKey:
    """FR-2.2: ручное создание ключа."""
    account = get_account(session, account_id)
    spec = CreateTokenSpec(
        name=name or "agw-manual",
        unlimited_quota=unlimited_quota,
        remain_quota=remain_quota if remain_quota is not None else 500_000,
        expired_time=expired_time,
        model_limits_enabled=model_limits_enabled,
        model_limits=model_limits,
    )
    creds = creds_of(box, account)
    info = await adapter.create_token(creds, spec)
    full = info.key_full or ""
    row = orm.ProviderKey(
        account_id=account_id,
        name=spec.name,
        key_enc=box.encrypt("key_enc", full),
        key_masked=mask_secret(full),
        provider_token_id=info.provider_token_id,
        unlimited_quota=unlimited_quota,
        remain_quota=None if unlimited_quota else spec.remain_quota,
        expired_time=expired_time,
        model_limits_enabled=model_limits_enabled,
        model_limits=model_limits if model_limits_enabled else None,
        status=orm.KeyStatus.active,
        created_via=orm.KeyOrigin.manual,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def decrypt_key(box: SecretBox, row: orm.ProviderKey) -> str:
    return box.decrypt("key_enc", row.key_enc) or ""


async def toggle_provider_key(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, key_id: int, enabled: bool
) -> orm.ProviderKey:
    """FR-2.3: включить/выключить — PUT статуса у провайдера (new-api: 1=активен, 2=выключен)."""
    row = await get_provider_key(session, key_id)
    account = get_account(session, row.account_id)
    creds: ProviderCredentials = creds_of(box, account)
    await adapter.update_token(
        creds, row.provider_token_id, {"status": 1 if enabled else 2}
    )
    row.status = orm.KeyStatus.active if enabled else orm.KeyStatus.disabled
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_provider_key(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, key_id: int
) -> None:
    """FR-2.3: удалить у провайдера + локально."""
    row = await get_provider_key(session, key_id)
    account = get_account(session, row.account_id)
    creds = creds_of(box, account)
    if row.provider_token_id is not None:
        try:
            await adapter.delete_token(creds, row.provider_token_id)
        except ProviderAuthError:
            raise
        except Exception as exc:
            log.warning("Удаление ключа %s у провайдера не удалось: %s", row.key_masked, exc)
            await log_system(
                session, "warn", "sync", f"Удаление ключа {row.key_masked} у провайдера не удалось: {exc}",
                account_id=account.id,
            )
    await session.delete(row)
    await session.commit()


async def rename_provider_key(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, key_id: int, name: str
) -> orm.ProviderKey:
    row = await get_provider_key(session, key_id)
    account = get_account(session, row.account_id)
    await adapter.update_token(creds_of(box, account), row.provider_token_id, {"name": name})
    row.name = name
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def sync_keys_usage(session: AsyncSession, adapter: ProviderAdapter, box: SecretBox) -> int:
    """Синк расхода ключей (used_quota/status) из списка /api/token/ (FR-2.3)."""
    accounts = (
        await session.exec(
            select(orm.Account).where(orm.Account.status == orm.AccountStatus.active)  # type: ignore[attr-defined]
        )
    ).all()
    updated = 0
    for acc in accounts:
        try:
            remote = await adapter.list_tokens(creds_of(box, acc))
        except Exception as exc:
            await log_system(session, "warn", "sync", f"{acc.name}: синк ключей не удался: {exc}", account_id=acc.id)
            continue
        by_id = {t.provider_token_id: t for t in remote if t.provider_token_id is not None}
        local_keys = (
            await session.exec(
                select(orm.ProviderKey).where(orm.ProviderKey.account_id == acc.id)  # type: ignore[attr-defined]
            )
        ).all()
        for k in local_keys:
            info = by_id.get(k.provider_token_id)
            if info is None:
                continue
            k.used_quota = info.used_quota
            k.unlimited_quota = info.unlimited_quota
            k.remain_quota = info.remain_quota
            if info.status is not None and info.status != 1 and k.status is orm.KeyStatus.active:
                k.status = orm.KeyStatus.disabled
            session.add(k)
            updated += 1
    await session.commit()
    return updated


async def pick_active_key_for_account(
    session: AsyncSession, box: SecretBox, account_id: int
) -> tuple[orm.ProviderKey, str]:
    """Активный ключ аккаунта для роутинга: auto-ключ в приоритете (§8.2 шаг 2)."""
    rows = (
        await session.exec(
            select(orm.ProviderKey)
            .where(orm.ProviderKey.account_id == account_id)  # type: ignore[attr-defined]
            .order_by(orm.ProviderKey.created_at.asc())  # type: ignore[attr-defined]
        )
    ).all()
    active = [r for r in rows if r.status is orm.KeyStatus.active]
    if not active:
        raise NotFoundError(f"У аккаунта {account_id} нет активных ключей провайдера")
    chosen = next((r for r in active if r.created_via is orm.KeyOrigin.auto), active[0])
    return chosen, await decrypt_key(box, chosen)


def expired_time_to_dt(expired_time: int) -> datetime | None:
    return None if expired_time in (-1, 0) else datetime.fromtimestamp(expired_time)
