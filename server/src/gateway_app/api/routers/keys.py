"""Роутер ключей шлюза (§7, FR-5)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gateway_app.api.deps import BoxDep, SessionDep
from gateway_app.core.errors import ValidationError
from gateway_app.domain.keys import gateway_keys as gk_service

router = APIRouter(prefix="/api/keys", tags=["gateway-keys"])


class GatewayKeyCreateIn(BaseModel):
    name: str = Field(max_length=100)
    token_limit: int | None = None  # None = безлимит
    expires_at: datetime | None = None  # None = бессрочно
    allowed_models: list[str] | None = None


class GatewayKeyPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    token_limit: int | None = None
    unset_token_limit: bool = False
    expires_at: datetime | None = None
    unset_expires_at: bool = False
    allowed_models: list[str] | None = None
    enabled: bool | None = None


@router.get("")
async def list_keys(session: SessionDep) -> dict[str, Any]:
    rows = await gk_service.list_gateway_keys(session)
    return {"success": True, "data": [gk_service.gateway_key_out(r) for r in rows]}


@router.post("")
async def create_key(body: GatewayKeyCreateIn, session: SessionDep, box: BoxDep) -> dict[str, Any]:
    if body.token_limit is not None and body.token_limit <= 0:
        raise ValidationError("token_limit должен быть положительным")
    row, full = await gk_service.create_gateway_key(
        session, box, name=body.name, token_limit=body.token_limit,
        expires_at=_ensure_utc(body.expires_at), allowed_models=body.allowed_models,
    )
    # полный ключ — один раз в ответе (FR-5.1)
    return {"success": True, "data": gk_service.gateway_key_out(row, full_key=full)}


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Наивные даты трактуем как UTC (в БД — aware UTC)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("/{key_id}/reveal")
async def reveal(key_id: int, session: SessionDep, box: BoxDep) -> dict[str, Any]:
    """FR-2.3/5.1: показать полный ключ по кнопке."""
    full = await gk_service.reveal_gateway_key(session, box, key_id)
    return {"success": True, "data": {"key": full}}


@router.patch("/{key_id}")
async def patch_key(key_id: int, body: GatewayKeyPatchIn, session: SessionDep) -> dict[str, Any]:
    row = await gk_service.patch_gateway_key(
        session, key_id,
        name=body.name,
        token_limit=body.token_limit,
        token_limit_unset=body.unset_token_limit,
        expires_at=_ensure_utc(body.expires_at),
        expires_unset=body.unset_expires_at,
        allowed_models=body.allowed_models,
        enabled=body.enabled,
    )
    return {"success": True, "data": gk_service.gateway_key_out(row)}


@router.delete("/{key_id}")
async def delete_key(key_id: int, session: SessionDep) -> dict[str, Any]:
    await gk_service.delete_gateway_key(session, key_id)
    return {"success": True, "data": {"deleted": True}}
