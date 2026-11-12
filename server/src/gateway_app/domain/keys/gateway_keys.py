"""Ключи шлюза (agw-...): выдача, аутентификация, лимиты токенов (FR-5)."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from gateway_app.core.errors import NotFoundError
from gateway_app.core.security import SecretBox, mask_secret
from gateway_app.db import orm
from gateway_app.db.orm import utcnow

# Результат аутентификации: (ключ, причина отказа)
# причины: None — ок; 'disabled' | 'expired' | 'limit' — не пропускаем
AuthResult = tuple[orm.GatewayKey | None, str | None]


def gateway_key_out(k: orm.GatewayKey, full_key: str | None = None) -> dict[str, Any]:
    out = {
        "id": k.id,
        "name": k.name,
        "key_masked": k.key_masked,
        "token_limit": k.token_limit,
        "tokens_used": k.tokens_used,
        "request_count": k.request_count,
        "allowed_models": k.allowed_models or [],
        "expires_at": k.expires_at,
        "status": k.status.value if isinstance(k.status, orm.KeyStatus) else k.status,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
    }
    if full_key is not None:
        out["full_key"] = full_key  # показывается один раз при создании / по кнопке
    return out


async def list_gateway_keys(session: AsyncSession) -> list[orm.GatewayKey]:
    result = await session.exec(select(orm.GatewayKey).order_by(orm.GatewayKey.created_at.desc()))  # type: ignore[attr-defined]
    return list(result.all())


async def create_gateway_key(
    session: AsyncSession,
    box: SecretBox,
    *,
    name: str,
    token_limit: int | None,
    expires_at: datetime | None,
    allowed_models: list[str] | None,
) -> tuple[orm.GatewayKey, str]:
    """Создать ключ шлюза; полный ключ возвращается один раз (FR-5.1)."""
    full = SecretBox.generate_gateway_key()
    row = orm.GatewayKey(
        name=name or "без имени",
        key_enc=box.encrypt("key_enc", full),
        key_hash=box.key_hash(full),
        key_masked=mask_secret(full, 4, 4),
        token_limit=token_limit,  # None = безлимит
        allowed_models=allowed_models or None,  # None/[] = все модели каталога
        expires_at=expires_at,
        status=orm.KeyStatus.active,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, full


async def get_gateway_key(session: AsyncSession, key_id: int) -> orm.GatewayKey:
    row = await session.get(orm.GatewayKey, key_id)
    if row is None:
        raise NotFoundError(f"Ключ шлюза {key_id} не найден")
    return row


async def reveal_gateway_key(session: AsyncSession, box: SecretBox, key_id: int) -> str:
    """Показать полный ключ (кнопка «Показать»)."""
    row = await get_gateway_key(session, key_id)
    return box.decrypt("key_enc", row.key_enc) or ""


async def patch_gateway_key(
    session: AsyncSession,
    key_id: int,
    *,
    name: str | None = None,
    token_limit: int | None = None,
    token_limit_unset: bool = False,
    expires_at: datetime | None = None,
    expires_unset: bool = False,
    allowed_models: list[str] | None = None,
    enabled: bool | None = None,
) -> orm.GatewayKey:
    """FR-5.4: редактирование лимита/моделей/срока + вкл/выкл."""
    row = await get_gateway_key(session, key_id)
    if name is not None:
        row.name = name
    if token_limit_unset:
        row.token_limit = None
    elif token_limit is not None:
        row.token_limit = token_limit
    if expires_unset:
        row.expires_at = None
    elif expires_at is not None:
        row.expires_at = expires_at
    if allowed_models is not None:
        row.allowed_models = allowed_models or None
    if enabled is not None:
        row.status = orm.KeyStatus.active if enabled else orm.KeyStatus.disabled
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_gateway_key(session: AsyncSession, key_id: int) -> None:
    row = await get_gateway_key(session, key_id)
    await session.delete(row)
    await session.commit()


async def authenticate_key(session: AsyncSession, box: SecretBox, presented: str) -> AuthResult:
    """Аутентификация по Bearer-ключу шлюза (§8.1)."""
    if not presented:
        return None, "missing"
    rows = (await session.exec(select(orm.GatewayKey))).all()
    h = box.key_hash(presented)
    for row in rows:
        if row.key_hash == h:
            return row, None
    return None, "invalid"


def check_key_usable(key: orm.GatewayKey, *, model: str | None = None) -> str | None:
    """Проверки статуса/срока/лимита/моделей (FR-5.3, §8.1). Возвращает причину отказа или None."""
    if key.status is not orm.KeyStatus.active:
        return "disabled"
    if key.expires_at is not None and key.expires_at < utcnow():
        return "expired"
    if key.token_limit is not None and key.tokens_used >= key.token_limit:
        return "limit"
    if model is not None and key.allowed_models and model not in key.allowed_models:
        return "model_not_allowed"
    return None
