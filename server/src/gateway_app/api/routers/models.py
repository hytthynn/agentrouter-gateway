"""Роутер каталога моделей (§7)."""

from typing import Any

from fastapi import APIRouter

from gateway_app.api.deps import SessionDep, get_adapter
from gateway_app.automation import manager
from gateway_app.automation.tasks import AutomationTask
from gateway_app.core.background import spawn
from gateway_app.core.errors import NotFoundError
from gateway_app.core.security import get_secret_box
from gateway_app.db import orm
from gateway_app.db.database import make_session
from gateway_app.domain.catalog import models_sync

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(session: SessionDep, search: str | None = None) -> dict[str, Any]:
    data = await models_sync.catalog_view(session, search)
    return {"success": True, "data": data}


@router.post("/sync", status_code=202)
async def sync_models() -> dict[str, Any]:
    """Синк каталога фоном → task_id (прогресс не обязателен, UI просто обновит список)."""
    task = manager.create("models_sync")
    adapter = get_adapter()
    box = get_secret_box()

    async def body(t: AutomationTask) -> dict[str, Any]:
        async with make_session() as session:
            t.log("Синхронизация каталога моделей…")
            result = await models_sync.sync_catalog(session, adapter, box)
            t.log(f"Готово: {result['catalog']} моделей")
            return result

    spawn(manager.run(task, body))
    return {"success": True, "data": {"task_id": task.id}}


@router.patch("/{model_id}")
async def patch_model(model_id: str, payload: dict[str, Any], session: SessionDep) -> dict[str, Any]:
    if "enabled" not in payload:
        from gateway_app.core.errors import ValidationError

        raise ValidationError("Требуется поле enabled: bool")
    await models_sync.set_model_enabled(session, model_id, bool(payload["enabled"]))
    row = await session.get(orm.ModelCatalog, model_id)
    if row is None:
        raise NotFoundError(f"Модель {model_id} не найдена")
    return {"success": True, "data": {"model_id": row.model_id, "enabled": row.enabled}}
