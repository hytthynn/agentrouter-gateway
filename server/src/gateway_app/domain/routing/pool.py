"""Пул аккаунтов для маршрутизации (FR-6.3): активные, с моделью, с балансом; probation — в конец."""

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from gateway_app.db import orm


async def candidates_for_model(session: AsyncSession, model_id: str) -> list[orm.Account]:
    """Кандидаты: status=active + модель доступна + баланс > 0 (или ещё неизвестен)."""
    rows = (
        await session.exec(
            select(orm.Account).where(  # type: ignore[attr-defined]
                orm.Account.status == orm.AccountStatus.active,
                or_(orm.Account.balance_quota.is_(None), orm.Account.balance_quota > 0),
            )
        )
    ).all()
    links = (
        await session.exec(
            select(orm.AccountModel).where(  # type: ignore[attr-defined]
                orm.AccountModel.model_id == model_id,
                orm.AccountModel.available == True,  # noqa: E712
            )
        )
    ).all()
    with_model = {ln.account_id for ln in links}
    out = [a for a in rows if a.id in with_model]
    # probation — в конец очереди
    out.sort(key=lambda a: 1 if (a.group_name or "default") == "probation" else 0)
    return out
