"""Менеджер задач автоматизации: SSE-прогресс, глобальная последовательность (§9.3), waiting_user_code."""

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from gateway_app.core.errors import AutomationError
from gateway_app.core.logging import get_logger

log = get_logger(__name__)


class TaskState(StrEnum):
    running = "running"
    waiting_user_code = "waiting_user_code"  # §9.5 — device verification
    success = "success"
    error = "error"


@dataclass
class TaskStep:
    key: str
    title: str
    status: str = "pending"  # pending | running | done | error


@dataclass
class AutomationTask:
    kind: str  # add_account | relogin | ...
    account_id: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    state: TaskState = TaskState.running
    steps: list[TaskStep] = field(default_factory=list)
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=400))
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _code_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _user_code: str | None = field(default=None, repr=False)
    _subscribers: set[asyncio.Queue] = field(default_factory=set, repr=False)

    # ---- прогресс ----
    def add_step(self, key: str, title: str) -> None:
        self.steps.append(TaskStep(key=key, title=title))
        self.publish({"type": "steps", "steps": self.steps_snapshot()})

    def step_status(self, key: str, status: str) -> None:
        for s in self.steps:
            if s.key == key:
                s.status = status
                break
        self.publish({"type": "steps", "steps": self.steps_snapshot()})

    def log(self, line: str) -> None:
        self.logs.append(line)
        self.publish({"type": "log", "line": line})

    def steps_snapshot(self) -> list[dict[str, str]]:
        return [{"key": s.key, "title": s.title, "status": s.status} for s in self.steps]

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.id,
            "kind": self.kind,
            "account_id": self.account_id,
            "state": self.state.value,
            "steps": self.steps_snapshot(),
            "logs": list(self.logs)[-100:],
            "error": self.error,
            "result": self.result,
        }

    # ---- подписки (SSE) ----
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Подписчик задачи %s переполнен — событие потеряно", self.id)

    def finish_success(self, result: dict[str, Any] | None = None) -> None:
        self.state = TaskState.success
        self.result = result or {}
        self.publish({"type": "state", "state": self.state.value, "result": self.result})

    def finish_error(self, message: str) -> None:
        self.state = TaskState.error
        self.error = message
        self.publish({"type": "state", "state": self.state.value, "error": message})

    # ---- device verification (§9.5) ----
    async def wait_user_code(self, timeout_s: float = 300.0) -> str:
        self.state = TaskState.waiting_user_code
        self.log("⏳ GitHub запрашивает код из письма — введите его в окне приложения")
        self.publish({"type": "state", "state": self.state.value})
        self._code_event.clear()
        try:
            await asyncio.wait_for(self._code_event.wait(), timeout=timeout_s)
        except TimeoutError as exc:
            raise AutomationError(
                "Код device-verification не введён за 5 минут — задача прервана"
            ) from exc
        self.state = TaskState.running
        self.publish({"type": "state", "state": self.state.value})
        code = self._user_code or ""
        self.log(f"Код принят ({len(code)} символов), продолжаю")
        return code

    def provide_user_code(self, code: str) -> bool:
        if self.state is not TaskState.waiting_user_code:
            return False
        self._user_code = code
        self._code_event.set()
        return True


class TaskManager:
    """Реестр задач + глобальный последовательный запуск (один браузер-логин за раз, §9.3)."""

    def __init__(self) -> None:
        self._tasks: dict[str, AutomationTask] = {}
        self._chain: asyncio.Lock = asyncio.Lock()

    def create(self, kind: str, account_id: int | None = None) -> AutomationTask:
        task = AutomationTask(kind=kind, account_id=account_id)
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> AutomationTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Задача {task_id} не найдена")
        return task

    async def run(self, task: AutomationTask, body) -> None:
        """Выполнить задачу последовательно с другими (глобальная блокировка §9.3)."""
        async with self._chain:
            try:
                result = await body(task)
                task.finish_success(result or {})
            except AutomationError as exc:
                task.finish_error(str(exc))
            except Exception as exc:
                log.exception("Задача %s упала", task.id)
                task.finish_error(f"{type(exc).__name__}: {exc}")

    async def stream_events(self, task: AutomationTask) -> AsyncIterator[dict[str, Any]]:
        """SSE-поток: снапшот + живые события до терминального состояния."""
        q = task.subscribe()
        try:
            yield {"type": "snapshot", **task.snapshot()}
            while True:
                if task.state in (TaskState.success, TaskState.error):
                    yield {"type": "snapshot", **task.snapshot()}
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield event
                except TimeoutError:
                    yield {"type": "ping"}
        finally:
            task.unsubscribe(q)

    def cleanup(self, max_age_s: int = 3600) -> int:
        """Удалить старые завершённые задачи (утечка памяти при долгом аптайме)."""
        now = datetime.now(UTC)
        to_del = [
            tid
            for tid, t in self._tasks.items()
            if t.state in (TaskState.success, TaskState.error)
            and (now - t.created_at).total_seconds() > max_age_s
        ]
        for tid in to_del:
            del self._tasks[tid]
        return len(to_del)


manager = TaskManager()
