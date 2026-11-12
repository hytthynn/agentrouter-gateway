"""Фоновые asyncio-задачи: удержание ссылок (RUF006) + тихое логирование падений."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from gateway_app.core.logging import get_logger

log = get_logger(__name__)

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Запустить фоновую задачу с удержанием ссылки и логом исключений."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Фоновая задача упала: %r", exc)
