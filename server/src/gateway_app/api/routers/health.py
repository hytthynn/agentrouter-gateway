"""Health (FR-10.3, §10.3): БД, шифрование, апстрим, версия платформы."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from gateway_app import __version__
from gateway_app.api.deps import SessionDep, get_adapter
from gateway_app.core.config import get_config
from gateway_app.core.security import get_secret_box

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health(session: SessionDep) -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {"version": __version__, "db": "ok", "encryption": "ok"}

    # БД
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        out["db"] = f"error: {exc}"

    # Шифрование: расшифровка самопроверки
    try:
        box = get_secret_box()
        probe = box.encrypt("_health", "probe")
        if box.decrypt("_health", probe) != "probe":
            raise RuntimeError("self-check mismatch")
    except Exception as exc:
        out["encryption"] = f"error: {exc}"

    # Апстрим: публичный /api/status
    upstream_reachable = False
    provider_version = None
    try:
        base = (await _base_url(session)) or cfg.default_base_url
        status = await get_adapter().platform_status(base)
        upstream_reachable = True
        provider_version = status.version
    except Exception as exc:
        out["upstream_error"] = str(exc)[:200]
    out["upstream_reachable"] = upstream_reachable
    out["provider_version"] = provider_version
    out["master_key_source"] = "env" if cfg.master_key else "file"
    return {"success": True, "data": out}


async def _base_url(session: Any) -> str | None:
    try:
        from gateway_app.settings_store import get_setting

        return await get_setting(session, "provider_base_url")
    except Exception:
        return None
