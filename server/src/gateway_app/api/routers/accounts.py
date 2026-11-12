"""Роутер аккаунтов (§7) + SSE задач автоматизации."""

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlmodel import select

from gateway_app.api.deps import SessionDep, get_adapter
from gateway_app.automation import manager
from gateway_app.automation.tasks import AutomationTask
from gateway_app.core.background import spawn
from gateway_app.core.config import get_config
from gateway_app.core.security import get_secret_box
from gateway_app.db import orm
from gateway_app.db.database import make_session
from gateway_app.domain.accounts import service as accounts_service
from gateway_app.domain.accounts.diagnostics import diagnose_account
from gateway_app.domain.accounts.schemas import (
    AccountCreateIn,
    AccountPatchIn,
    DeleteResult,
    ReloginAccepted,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
async def list_accounts(session: SessionDep) -> dict[str, Any]:
    rows = await accounts_service.list_accounts(session)
    return {"success": True, "data": [accounts_service.account_out(a) for a in rows]}


@router.post("", status_code=202)
async def add_account(body: AccountCreateIn) -> dict[str, Any]:
    """FR-1.2: добавить аккаунт → 202 + task_id (автоматизация фоном, прогресс по SSE)."""
    task = manager.create("add_account")
    data = body.model_dump()
    cfg = get_config()
    adapter = get_adapter()

    async def body_fn(t: AutomationTask) -> dict[str, Any]:
        return await accounts_service.add_account_flow(
            t, make_session, adapter, data, default_base_url=cfg.default_base_url
        )

    spawn(manager.run(task, body_fn))
    return {"success": True, "data": {"task_id": task.id, "state": task.state.value}}


@router.get("/{account_id}")
async def get_account(account_id: int, session: SessionDep) -> dict[str, Any]:
    row = await accounts_service.get_account(session, account_id)
    return {"success": True, "data": accounts_service.account_out(row)}


@router.patch("/{account_id}")
async def patch_account(account_id: int, body: AccountPatchIn, session: SessionDep) -> dict[str, Any]:
    row = await accounts_service.patch_account(
        session, account_id, name=body.name, base_url=body.base_url, enabled=body.enabled
    )
    return {"success": True, "data": accounts_service.account_out(row)}


@router.delete("/{account_id}")
async def delete_account(
    account_id: int, session: SessionDep, purge_provider_keys: bool = False
) -> dict[str, Any]:
    deleted_remote, errors = await accounts_service.delete_account(
        session, get_adapter(), get_secret_box(), account_id,
        purge_provider_keys=purge_provider_keys,
    )
    return {
        "success": True,
        "data": DeleteResult(
            deleted=True, provider_keys_deleted=deleted_remote, errors=errors
        ).model_dump(),
    }


@router.post("/{account_id}/checkin")
async def checkin(account_id: int, session: SessionDep) -> dict[str, Any]:
    from gateway_app.domain.checkin.service import run_checkin_by_id
    from gateway_app.settings_store import get_setting

    relogin = (await get_setting(session, "checkin_relogin_after")) == "true"
    result = await run_checkin_by_id(
        session, get_adapter(), get_secret_box(), account_id, relogin_after=relogin
    )
    return {"success": True, "data": result}


@router.post("/{account_id}/refresh")
async def refresh(account_id: int, session: SessionDep) -> dict[str, Any]:
    row = await accounts_service.refresh_balance_by_id(
        session, get_adapter(), get_secret_box(), account_id
    )
    return {"success": True, "data": accounts_service.account_out(row)}


@router.post("/{account_id}/relogin", status_code=202)
async def relogin(account_id: int, session: SessionDep) -> dict[str, Any]:
    await accounts_service.get_account(session, account_id)
    task = manager.create("relogin", account_id=account_id)
    spawn(
        manager.run(
            task, lambda t: accounts_service.relogin_flow(t, make_session, get_adapter(), account_id)
        )
    )
    return {"success": True, "data": ReloginAccepted(task_id=task.id).model_dump()}


@router.post("/{account_id}/diagnose")
async def diagnose(account_id: int, session: SessionDep) -> dict[str, Any]:
    result = await diagnose_account(session, get_adapter(), get_secret_box(), account_id)
    return {"success": True, "data": result}


@router.get("/{account_id}/models")
async def account_models(account_id: int, session: SessionDep) -> dict[str, Any]:
    await accounts_service.get_account(session, account_id)
    links = (
        await session.exec(
            select(orm.AccountModel).where(orm.AccountModel.account_id == account_id)  # type: ignore[attr-defined]
        )
    ).all()
    return {
        "success": True,
        "data": [
            {"model_id": ln.model_id, "available": ln.available, "checked_at": ln.checked_at}
            for ln in links
        ],
    }


# ------------------------------------------------------------------- SSE задач


router_tasks = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router_tasks.get("/{task_id}/events")
async def task_events(task_id: str, request: Request) -> StreamingResponse:
    """SSE прогресса автоматизации (§7): шаги + логи + состояния."""

    async def gen():
        try:
            task = manager.get(task_id)
        except KeyError:
            yield f"data: {json.dumps({'type': 'error', 'error': 'задача не найдена'})}\n\n"
            return
        async for event in manager.stream_events(task):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "snapshot" and event.get("state") in ("success", "error"):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router_tasks.post("/{job}/run")
async def run_job(job: str, session: SessionDep) -> dict[str, Any]:
    """Ручной триггер джоб планировщика (§11)."""
    from gateway_app.core.errors import NotFoundError
    from gateway_app.scheduler.jobs import JOB_NAMES, trigger_job

    if job not in JOB_NAMES:
        raise NotFoundError(f"Джоба {job!r} не найдена; доступно: {', '.join(sorted(JOB_NAMES))}")
    result = await trigger_job(job)
    return {"success": True, "data": {"job": job, "result": result}}


@router_tasks.post("/{task_id}/code")
async def provide_code(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """§9.5: пользователь ввёл device-verification код."""
    code = str(payload.get("code") or "").strip()
    if not code:
        return {"success": False, "error": {"code": "validation_error", "message": "Код пуст"}}
    task = manager.get(task_id)
    ok = task.provide_user_code(code)
    return {"success": ok, "data": {"state": task.state.value}}
