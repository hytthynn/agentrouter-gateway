"""Роутер логов (§7, FR-7)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from gateway_app.api.deps import SessionDep
from gateway_app.domain.logs import service as logs_service

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/requests")
async def request_logs(
    session: SessionDep,
    status: str | None = None,
    gateway_key_id: int | None = None,
    account_id: int | None = None,
    model: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    data = await logs_service.query_request_logs(
        session, status=status, gateway_key_id=gateway_key_id, account_id=account_id,
        model=model, search=search, since=since, until=until,
        page=max(1, page), page_size=min(200, max(1, page_size)),
    )
    return {"success": True, "data": data}


@router.get("/system")
async def system_logs(
    session: SessionDep,
    level: str | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    data = await logs_service.query_system_logs(
        session, level=level, category=category, account_id=account_id, search=search,
        page=max(1, page), page_size=min(200, max(1, page_size)),
    )
    return {"success": True, "data": data}
