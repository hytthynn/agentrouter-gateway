"""Синхронизация каталога моделей (FR-4): union моделей активных аккаунтов."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from gateway_app.core.logging import get_logger
from gateway_app.core.security import SecretBox
from gateway_app.db import orm
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.base import creds_of, list_accounts
from gateway_app.domain.logs.service import log_system
from gateway_app.providers.base import ProviderAdapter

log = get_logger(__name__)


async def sync_account_models(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, account: orm.Account
) -> int:
    """Синк одного аккаунта → upsert каталога + account_models. Возвращает размер каталога."""
    model_ids = await adapter.list_models(creds_of(box, account))
    now = utcnow()
    for mid in model_ids:
        row = await session.get(orm.ModelCatalog, mid)
        if row is None:
            session.add(
                orm.ModelCatalog(model_id=mid, enabled=True, added_from_account_id=account.id)
            )
    # account_models: доступность
    current = set(model_ids)
    existing = (
        await session.exec(
            select(orm.AccountModel).where(orm.AccountModel.account_id == account.id)  # type: ignore[attr-defined]
        )
    ).all()
    seen: set[str] = set()
    for link in existing:
        link.available = link.model_id in current
        link.checked_at = now
        session.add(link)
        seen.add(link.model_id)
    for mid in current - seen:
        session.add(
            orm.AccountModel(account_id=account.id, model_id=mid, available=True, checked_at=now)  # type: ignore[arg-type]
        )
    await session.commit()
    return len(await session.exec(select(orm.ModelCatalog)).all())  # type: ignore[attr-defined]


async def sync_catalog(session: AsyncSession, adapter: ProviderAdapter, box: SecretBox) -> dict[str, int]:
    """FR-4.1: union моделей всех активных аккаунтов."""
    accounts = await list_accounts(session)
    active = [a for a in accounts if a.status is orm.AccountStatus.active]
    total_models = 0
    for acc in active:
        try:
            total_models = await sync_account_models(session, adapter, box, acc)
        except Exception as exc:
            log.warning("Синк моделей %s не удался: %s", acc.name, exc)
            await log_system(
                session, "warn", "sync", f"Синк моделей {acc.name} не удался: {exc}", account_id=acc.id
            )
    catalog_size = len((await session.exec(select(orm.ModelCatalog))).all())  # type: ignore[attr-defined]
    await log_system(
        session, "info", "sync", f"Каталог моделей: {catalog_size} моделей по {len(active)} аккаунтам"
    )
    return {"catalog": catalog_size, "last_account": total_models, "accounts": len(active)}


async def catalog_view(session: AsyncSession, search: str | None = None) -> list[dict[str, Any]]:
    """FR-4.2: модель → {enabled, доступность (N аккаунтов), источник}."""
    models = (
        await session.exec(select(orm.ModelCatalog).order_by(orm.ModelCatalog.model_id))  # type: ignore[attr-defined]
    ).all()
    links = (await session.exec(select(orm.AccountModel))).all()
    accounts = {a.id: a for a in await list_accounts(session)}
    by_model: dict[str, list[orm.AccountModel]] = {}
    for link in links:
        by_model.setdefault(link.model_id, []).append(link)

    out: list[dict[str, Any]] = []
    for m in models:
        if search and search.lower() not in m.model_id.lower():
            continue
        links_m = by_model.get(m.model_id, [])
        available = [ln for ln in links_m if ln.available]
        src = accounts.get(m.added_from_account_id) if m.added_from_account_id else None
        out.append(
            {
                "model_id": m.model_id,
                "enabled": m.enabled,
                "accounts_available": len(available),
                "accounts_total": len({ln.account_id for ln in links_m}),
                "no_active_accounts": len(available) == 0,
                "added_from": src.name if src else None,
                "created_at": m.created_at,
            }
        )
    return out


async def set_model_enabled(session: AsyncSession, model_id: str, enabled: bool) -> None:
    row = await session.get(orm.ModelCatalog, model_id)
    if row is None:
        row = orm.ModelCatalog(model_id=model_id, enabled=enabled)
    else:
        row.enabled = enabled
    session.add(row)
    await session.commit()


async def enabled_model_ids(session: AsyncSession) -> list[str]:
    rows = (
        await session.exec(
            select(orm.ModelCatalog).where(orm.ModelCatalog.enabled == True)  # noqa: E712
        )
    ).all()
    return [r.model_id for r in rows]
