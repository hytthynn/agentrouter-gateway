"""Роутер настроек (FR-10) + client-профили (§10.2)."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from gateway_app.api.deps import SessionDep
from gateway_app.core.errors import ValidationError
from gateway_app.providers.agentrouter.profiles import list_profiles
from gateway_app.settings_store import get_all, put_settings, subscribe_change

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPutIn(BaseModel):
    values: dict[str, str]


@router.get("")
async def get_settings(session: SessionDep) -> dict[str, Any]:
    return {"success": True, "data": await get_all(session)}


@router.put("")
async def update_settings(body: SettingsPutIn, session: SessionDep) -> dict[str, Any]:
    try:
        result = await put_settings(session, body.values)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return {"success": True, "data": result}


# ------------------------------------------------------------- профили

router_profiles = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router_profiles.get("")
async def get_profiles(session: SessionDep) -> dict[str, Any]:
    rows = await list_profiles(session)
    return {
        "success": True,
        "data": [
            {"name": p.name, "user_agent": p.user_agent, "extra_headers": p.extra_headers or {}, "is_default": p.is_default}
            for p in rows
        ],
    }


@router_profiles.put("/{name}")
async def put_profile(name: str, payload: dict[str, Any], session: SessionDep) -> dict[str, Any]:
    """Правка UA/заголовков профиля без пересборки (§10.2)."""
    from gateway_app.db import orm

    ua = str(payload.get("user_agent") or "").strip()
    if not ua:
        raise ValidationError("user_agent обязателен")
    extra = payload.get("extra_headers") or {}
    if not isinstance(extra, dict):
        raise ValidationError("extra_headers: ожидается объект строк")
    row = await session.get(orm.ClientProfile, name)
    if row is None:
        row = orm.ClientProfile(name=name, user_agent=ua, extra_headers=extra)
    else:
        row.user_agent = ua
        row.extra_headers = {str(k): str(v) for k, v in extra.items()}
    session.add(row)
    await session.commit()
    return {"success": True, "data": {"name": row.name, "user_agent": row.user_agent, "extra_headers": row.extra_headers or {}}}


_ = subscribe_change  # подписка используется планировщиком (§11 hot-reload)
