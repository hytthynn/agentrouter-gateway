"""Роутер ключей провайдера (§7)."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gateway_app.api.deps import SessionDep, get_adapter
from gateway_app.core.security import get_secret_box
from gateway_app.domain.keys import provider_keys as pk_service

router = APIRouter(prefix="/api/provider-keys", tags=["provider-keys"])


class ProviderKeyCreateIn(BaseModel):
    account_id: int
    name: str = Field(max_length=100)
    unlimited_quota: bool = False
    remain_quota: int | None = 500_000
    expired_time: int = -1  # -1 = бессрочно
    model_limits_enabled: bool = False
    model_limits: list[str] | None = None


class ProviderKeyPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None


@router.get("")
async def list_keys(session: SessionDep, account_id: int | None = None) -> dict[str, Any]:
    rows = await pk_service.list_keys(session, account_id=account_id)
    return {"success": True, "data": rows}


@router.post("")
async def create_key(body: ProviderKeyCreateIn, session: SessionDep) -> dict[str, Any]:
    row = await pk_service.create_manual_key(
        session, get_adapter(), get_secret_box(), body.account_id,
        name=body.name,
        unlimited_quota=body.unlimited_quota,
        remain_quota=body.remain_quota,
        expired_time=body.expired_time,
        model_limits_enabled=body.model_limits_enabled,
        model_limits=body.model_limits,
    )
    return {"success": True, "data": pk_service.provider_key_out(row)}


@router.patch("/{key_id}")
async def patch_key(key_id: int, body: ProviderKeyPatchIn, session: SessionDep) -> dict[str, Any]:
    if body.name is not None:
        await pk_service.rename_provider_key(session, get_adapter(), get_secret_box(), key_id, body.name)
    if body.enabled is not None:
        await pk_service.toggle_provider_key(session, get_adapter(), get_secret_box(), key_id, body.enabled)
    row = await pk_service.get_provider_key(session, key_id)
    return {"success": True, "data": pk_service.provider_key_out(row)}


@router.delete("/{key_id}")
async def delete_key(key_id: int, session: SessionDep) -> dict[str, Any]:
    await pk_service.delete_provider_key(session, get_adapter(), get_secret_box(), key_id)
    return {"success": True, "data": {"deleted": True}}
