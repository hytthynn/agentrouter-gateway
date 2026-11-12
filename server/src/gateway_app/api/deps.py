"""Общие зависимости API: сессия, SecretBox, адаптер провайдера."""

from typing import Annotated, Any

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.core.security import SecretBox, get_secret_box
from gateway_app.db.database import get_session

_adapter: Any = None


def _box_dep() -> SecretBox:
    return get_secret_box()


def get_adapter() -> Any:
    """Единственная реализация ProviderAdapter (в тестах переопределяется)."""
    global _adapter
    if _adapter is None:
        from gateway_app.providers.agentrouter import AgentRouterAdapter

        _adapter = AgentRouterAdapter()
    return _adapter


SessionDep = Annotated[AsyncSession, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(_box_dep)]
AdapterDep = Annotated[Any, Depends(get_adapter)]
