"""Client-профили имперсонации: разрешение имени → (User-Agent, доп. заголовки) из БД (§10.2)."""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.db import orm
from gateway_app.db.seed import SEED_PROFILES


async def resolve_profile(
    session: AsyncSession, name: str | None
) -> tuple[str | None, dict[str, str]]:
    """Вернуть (ua, headers) профиля; при отсутствии в БД — из seed; None → default."""
    wanted = name or next(p["name"] for p in SEED_PROFILES if p.get("is_default"))

    row = await session.get(orm.ClientProfile, wanted)
    if row is not None:
        return row.user_agent, dict(row.extra_headers or {})

    for spec in SEED_PROFILES:
        if spec["name"] == wanted:
            return spec["user_agent"], dict(spec["extra_headers"] or {})
    return None, {}


async def list_profiles(session: AsyncSession) -> list[orm.ClientProfile]:
    result = await session.exec(select(orm.ClientProfile).order_by(orm.ClientProfile.name))
    return list(result.all())
