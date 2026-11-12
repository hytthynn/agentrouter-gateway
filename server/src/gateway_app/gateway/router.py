"""Шлюз OpenAI-compatible: /v1/chat/completions (стрим/не-стрим), /v1/models (FR-6, §8).

Ключ шлюза → проверка лимитов → пул аккаунтов → failover (до max_attempts) → upstream
через client-профили → SSE-прокси → accounting. Семафоры (аккаунт/шлюз) держатся
на всё время ответа, включая стрим. Модели с только-Responses API переводятся
автоматически (responses_compat).
"""

import asyncio
import contextlib
import functools
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.api.deps import get_adapter
from gateway_app.core.background import spawn
from gateway_app.core.logging import get_logger
from gateway_app.core.masking import mask_text
from gateway_app.core.security import get_secret_box
from gateway_app.db import orm
from gateway_app.db.database import make_session
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.base import creds_of
from gateway_app.domain.catalog.models_sync import enabled_model_ids
from gateway_app.domain.keys.gateway_keys import authenticate_key, check_key_usable
from gateway_app.domain.keys.provider_keys import pick_active_key_for_account
from gateway_app.domain.routing.pool import candidates_for_model
from gateway_app.domain.routing.strategy import router_state
from gateway_app.domain.usage.accounting import estimate_tokens, record_usage
from gateway_app.gateway.compat import (
    auth_error,
    bad_request_error,
    error_response,
    not_found_error,
    provider_error_response,
    quota_error,
)
from gateway_app.gateway.responses_compat import (
    CHAT_PATH,
    RESPONSES_PATH,
    ResponsesLiveStream,
    chat_to_responses_payload,
    is_responses_required,
    responses_to_chat_response,
)
from gateway_app.providers.agentrouter import errors as ar_errors
from gateway_app.providers.agentrouter.profiles import resolve_profile
from gateway_app.providers.base import UpstreamResponse
from gateway_app.settings_store import load

log = get_logger(__name__)

router = APIRouter(tags=["gateway"])

# Семафоры (FR-6.7) — ленивая инициализация по настройкам
_gate_sem: asyncio.Semaphore | None = None
_gate_limit: int = 0
_account_sems: dict[int, asyncio.Semaphore] = {}
_SEM_TIMEOUT_S = 30.0

# Модели, требующие /v1/responses (детектится по ошибке апстрима, §2.6) — in-memory
_responses_only_models: set[str] = set()


class Deferred:
    """Отложенные освобождения ресурсов — вызываются по завершении ответа/стрима."""

    def __init__(self) -> None:
        self._fns: list[Callable[[], None]] = []
        self.done = False

    def add(self, fn: Callable[[], None]) -> None:
        self._fns.append(fn)

    def run(self) -> None:
        if self.done:
            return
        self.done = True
        for fn in self._fns:
            try:
                fn()
            except Exception:
                log.exception("Ошибка освобождения ресурса шлюза")


async def _acquire_gate(limit: int) -> bool:
    global _gate_sem, _gate_limit
    if _gate_sem is None or _gate_limit != limit:
        _gate_sem = asyncio.Semaphore(limit)
        _gate_limit = limit
    try:
        await asyncio.wait_for(_gate_sem.acquire(), timeout=_SEM_TIMEOUT_S)
        return True
    except TimeoutError:
        return False


def _release_gate() -> None:
    if _gate_sem is not None:
        _gate_sem.release()


async def _acquire_account(account_id: int, limit: int) -> bool:
    sem = _account_sems.get(account_id)
    if sem is None:
        sem = asyncio.Semaphore(limit)
        _account_sems[account_id] = sem
    try:
        await asyncio.wait_for(sem.acquire(), timeout=_SEM_TIMEOUT_S)
        return True
    except TimeoutError:
        return False


def _release_account(account_id: int) -> None:
    sem = _account_sems.get(account_id)
    if sem is not None:
        sem.release()


# ------------------------------------------------------------------ helpers


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _auth_message(reason: str | None) -> str:
    if reason == "missing":
        return "Отсутствует Authorization: Bearer <ключ шлюза agw-…>"
    return "Ключ шлюза не найден"


def _key_message(reason: str) -> str:
    return {
        "disabled": "Ключ шлюза выключен",
        "expired": "Ключ шлюза истёк",
        "limit": "Достигнут лимит токенов ключа шлюза",
        "model_not_allowed": "Модель не разрешена для этого ключа шлюза",
    }.get(reason, reason)


def _model_hint(model_id: str, row: orm.ModelCatalog | None) -> str:
    if row is None:
        return f"Модель {model_id!r} отсутствует в каталоге шлюза"
    return f"Модель {model_id!r} есть в каталоге, но выключена (включите на странице «Модели»)"


# ------------------------------------------------------------------ /v1/models


@router.get("/v1/models")
async def list_models(request: Request) -> Any:
    session = make_session()
    try:
        key, reason = await authenticate_key(session, get_secret_box(), _bearer(request))
        if key is None:
            return auth_error(_auth_message(reason))
        models = await enabled_model_ids(session)
        now = int(utcnow().timestamp())
        return {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": now, "owned_by": "agentrouter"}
                for m in models
            ],
        }
    finally:
        await session.close()


@router.get("/v1/models/{model_id}")
async def get_model(model_id: str, request: Request) -> Any:
    session = make_session()
    try:
        key, reason = await authenticate_key(session, get_secret_box(), _bearer(request))
        if key is None:
            return auth_error(_auth_message(reason))
        row = await session.get(orm.ModelCatalog, model_id)
        if row is None or not row.enabled:
            return not_found_error(_model_hint(model_id, row))
        now = int(utcnow().timestamp())
        return {"id": model_id, "object": "model", "created": now, "owned_by": "agentrouter"}
    finally:
        await session.close()


# --------------------------------------------------------- chat/completions


async def _close_session_safely(session: AsyncSession) -> None:
    """Закрыть сессию даже при отмене таски запроса (клиент оборвал соединение).

    CancelledError не глушим — протокол отмены должен соблюдаться; shield позволяет
    close() доиграть в фоне, чтобы пул неterminate'ил подключение с ошибкой.
    """
    with contextlib.suppress(Exception):
        await asyncio.shield(session.close())


def _spawn_accounting(fields: dict[str, Any]) -> None:
    """Accounting фоновым таском: не блокирует ответ и не рвётся при отмене запроса."""

    async def _write() -> None:
        s = make_session()
        try:
            await record_usage(s, **fields)
        except Exception:
            log.exception("Ошибка записи accounting")
        finally:
            with contextlib.suppress(Exception):
                await s.close()

    spawn(_write())


def _record_response(
    payload: dict[str, Any],
    http_status: int,
    body: dict[str, Any],
    *,
    started: float,
    first_token_ms: int | None,
    gateway_key_id: int | None,
    account_id: int | None,
    model: str,
    stream: bool,
    attempts: list[dict[str, Any]],
    client_ua: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    usage = (body or {}).get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    est = pt == 0 and ct == 0
    if est:
        pt, ct = estimate_tokens(payload.get("messages"), 0)
    cost_quota = usage.get("cost_quota") or usage.get("quota")
    _spawn_accounting(
        {
            "gateway_key_id": gateway_key_id,
            "account_id": account_id,
            "model": model,
            "stream": stream,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "estimated": est,
            "cost_quota": int(cost_quota) if cost_quota else None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "first_token_ms": first_token_ms,
            "http_status": http_status,
            "attempts": attempts or None,
            "client_ua": client_ua,
            "error_code": error_code,
            "error_message": error_message,
        }
    )


async def _safe_stream_close(stream_ctx: Any) -> None:
    with contextlib.suppress(Exception):
        await stream_ctx.__aexit__(None, None, None)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """Сценарий FR-6: auth → лимиты → роутинг → failover → SSE/JSON → accounting."""
    started = time.monotonic()
    box = get_secret_box()
    session = make_session()
    defer = Deferred()
    try:
        # 1. Аутентификация ключа шлюза
        key, reason = await authenticate_key(session, box, _bearer(request))
        if key is None:
            return auth_error(_auth_message(reason))
        usable = check_key_usable(key)
        if usable:
            if usable == "limit":
                return quota_error(_key_message(usable))
            return auth_error(_key_message(usable))

        # 2. Тело запроса
        try:
            payload = await request.json()
        except Exception:
            return bad_request_error("Тело запроса не является JSON")
        model = str(payload.get("model") or "")
        if not model:
            return bad_request_error("Не указано поле model")

        # 3. Модель: в каталоге и включена (§8.1)
        row = await session.get(orm.ModelCatalog, model)
        if row is None or not row.enabled:
            return not_found_error(_model_hint(model, row))
        allowed = check_key_usable(key, model=model)
        if allowed == "model_not_allowed":
            return error_response(403, "invalid_request_error", "model_not_allowed", _key_message(allowed))

        settings = await load(session)

        # 4. Параллелизм шлюза — держится до конца ответа (в т.ч. стрима)
        if not await _acquire_gate(settings.gateway_concurrency):
            return quota_error("Шлюз перегружен — слишком много параллельных запросов")
        defer.add(_release_gate)

        try:
            return await _with_failover(
                request, session, key, payload, model, settings, started, defer
            )
        except Exception:
            defer.run()
            raise
    finally:
        await _close_session_safely(session)


async def _with_failover(
    request: Request,
    session: AsyncSession,
    key: orm.GatewayKey,
    payload: dict[str, Any],
    model: str,
    settings: Any,
    started: float,
    defer: Deferred,
) -> Any:
    """FR-6.3/6.4: выбор аккаунта + failover до max_attempts; trace попыток в лог."""
    adapter = get_adapter()
    box = get_secret_box()
    max_attempts = settings.max_attempts
    stream = bool(payload.get("stream"))

    candidates = await candidates_for_model(session, model)
    if not candidates:
        return error_response(
            503, "api_error", "no_accounts",
            "Нет активных аккаунтов с доступной моделью и ненулевым балансом",
        )
    if router_state.name != settings.routing_strategy:
        router_state.configure(settings.routing_strategy)
    ordered = router_state.order(candidates)

    attempts: list[dict[str, Any]] = []
    last_exc: Exception | None = None
    profile_name = settings.default_client_profile
    ua, extra_headers = await resolve_profile(session, profile_name)

    for idx, account in enumerate(ordered):
        if idx >= max_attempts:
            break
        if not await _acquire_account(account.id or 0, settings.account_concurrency):
            attempts.append({"account_id": account.id, "status": "skipped", "code": "concurrency"})
            continue
        defer.add(functools.partial(_release_account, account.id or 0))

        try:
            try:
                pk_row, api_key = await pick_active_key_for_account(session, box, account.id or 0)
            except Exception as exc:
                attempts.append({"account_id": account.id, "status": "error", "code": str(exc)[:80]})
                last_exc = exc
                continue

            base_ua = request.headers.get("user-agent")
            common: dict[str, Any] = dict(
                gateway_key_id=key.id,
                account_id=account.id,
                model=model,
                stream=stream,
                attempts=attempts,
                client_ua=base_ua,
            )

            # Responses API: модель не поддерживает chat/completions → сразу /v1/responses
            upstream_path = RESPONSES_PATH if model in _responses_only_models else CHAT_PATH
            upstream_payload = (
                chat_to_responses_payload(payload) if upstream_path == RESPONSES_PATH else payload
            )

            if stream:
                live: Any
                try:
                    stream_ctx = adapter.chat_stream(
                        creds_of(box, account), api_key, upstream_payload,
                        profile_ua=ua, profile_headers=extra_headers,
                        timeout_s=settings.upstream_timeout_s, path=upstream_path,
                    )
                    live = await stream_ctx.__aenter__()
                except Exception as exc:
                    # модель требует Responses API → пробуем тем же аккаунтом (§8.4)
                    if upstream_path == CHAT_PATH and is_responses_required(exc):
                        _responses_only_models.add(model)
                        upstream_path = RESPONSES_PATH
                        upstream_payload = chat_to_responses_payload(payload)
                        try:
                            stream_ctx = adapter.chat_stream(
                                creds_of(box, account), api_key, upstream_payload,
                                profile_ua=ua, profile_headers=extra_headers,
                                timeout_s=settings.upstream_timeout_s, path=upstream_path,
                            )
                            live = await stream_ctx.__aenter__()
                        except Exception as exc2:
                            attempts.append(
                                {"account_id": account.id, "status": "error", "code": getattr(exc2, "code", type(exc2).__name__)}
                            )
                            last_exc = exc2
                            if ar_errors.is_retriable(exc2):
                                continue
                            defer.run()
                            return provider_error_response(exc2, attempts_left=[{'account_id': a.id} for a in ordered[idx + 1 :]])
                    else:
                        # до первого байта клиенту — failover возможен (§8.4)
                        attempts.append(
                            {"account_id": account.id, "status": "error", "code": getattr(exc, "code", type(exc).__name__)}
                        )
                        last_exc = exc
                        if ar_errors.is_retriable(exc):
                            continue
                        defer.run()
                        return provider_error_response(exc, attempts_left=[{'account_id': a.id} for a in ordered[idx + 1 :]])

                pk_row.last_used_at = utcnow()
                session.add(pk_row)
                await session.commit()
                if upstream_path == RESPONSES_PATH:
                    live = ResponsesLiveStream(live, model)
                return _stream_response(
                    live, stream_ctx, payload, base=common,
                    started=started, on_finish=defer.run,
                )

            try:
                resp = await adapter.chat_non_stream(
                    creds_of(box, account), api_key, upstream_payload,
                    profile_ua=ua, profile_headers=extra_headers,
                    timeout_s=settings.upstream_timeout_s, path=upstream_path,
                )
            except Exception as exc:
                attempts.append(
                    {"account_id": account.id, "status": "error", "code": getattr(exc, "code", type(exc).__name__)}
                )
                if upstream_path == CHAT_PATH and is_responses_required(exc):
                    # модель требует Responses API → переводим payload и пробуем тем же аккаунтом
                    _responses_only_models.add(model)
                    upstream_path = RESPONSES_PATH
                    upstream_payload = chat_to_responses_payload(payload)
                    try:
                        resp = await adapter.chat_non_stream(
                            creds_of(box, account), api_key, upstream_payload,
                            profile_ua=ua, profile_headers=extra_headers,
                            timeout_s=settings.upstream_timeout_s, path=upstream_path,
                        )
                    except Exception as exc2:
                        last_exc = exc2
                        if ar_errors.is_retriable(exc2):
                            continue
                        defer.run()
                        return provider_error_response(exc2, attempts_left=[{'account_id': a.id} for a in ordered[idx + 1 :]])
                else:
                    last_exc = exc
                    if ar_errors.is_retriable(exc):
                        continue
                    defer.run()
                    return provider_error_response(exc, attempts_left=[{'account_id': a.id} for a in ordered[idx + 1 :]])

            # успех (не-стрим): accounting + passthrough (с трансляцией ответа при Responses API)
            pk_row.last_used_at = utcnow()
            session.add(pk_row)
            await session.commit()
            if upstream_path == RESPONSES_PATH:
                resp = UpstreamResponse(
                    status=resp.status, body=responses_to_chat_response(resp.body)
                )
            _record_response(
                payload, resp.status, resp.body,
                started=started, first_token_ms=None, **common,
            )
            defer.run()
            return JSONResponse(status_code=200, content=resp.body)
        except Exception:
            # неожиданная ошибка внутри попытки — освобождаем и пробрасываем
            raise

    # попытки исчерпаны
    defer.run()
    if isinstance(last_exc, ar_errors.BudgetPoolExhaustedError):
        return provider_error_response(last_exc, attempts_left=[])
    return error_response(
        502, "api_error", "all_attempts_failed",
        f"Все попытки ({len(attempts)}) не удались: "
        + mask_text(str(last_exc) if last_exc else "неизвестно"),
    )


def _stream_response(
    live: Any,
    stream_ctx: Any,
    payload: dict[str, Any],
    *,
    base: dict[str, Any],
    started: float,
    on_finish: Callable[[], None],
) -> StreamingResponse:
    """SSE-прокси: чанк за чанком, TTFT, usage из финального чанка (FR-6.2, §8.4)."""

    async def gen() -> AsyncIterator[bytes]:
        first_ts: float | None = None
        abort = False
        error_chunk: str | None = None
        try:
            async for chunk in live.aiter_bytes():
                if first_ts is None:
                    first_ts = time.monotonic()
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            abort = True  # обрыв клиента — апстрим закрываем (FR-6.6)
            raise
        except Exception as exc:  # после первого байта failover невозможен
            error_chunk = str(exc)
            err = json.dumps(
                {"error": {"type": "api_error", "code": "upstream_error", "message": mask_text(str(exc))}},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n".encode()
        finally:
            usage = getattr(live, "last_usage", None)
            pt = int(usage.get("prompt_tokens") or 0) if usage else 0
            ct = int(usage.get("completion_tokens") or 0) if usage else 0
            est = pt == 0 and ct == 0
            if est:
                pt, ct = estimate_tokens(payload.get("messages"), getattr(live, "completion_chars", 0))
            duration = int((time.monotonic() - started) * 1000)
            ttft = int((first_ts - started) * 1000) if first_ts else None
            cost = (usage.get("cost_quota") or usage.get("quota")) if usage else None
            status = (
                orm.RequestStatus.client_abort
                if abort
                else (orm.RequestStatus.error if error_chunk else orm.RequestStatus.success)
            )
            # accounting фоном: не блокируем закрытие генератора и не рвёмся при отмене
            _spawn_accounting(
                {
                    "gateway_key_id": base["gateway_key_id"],
                    "account_id": base["account_id"],
                    "model": base["model"],
                    "stream": True,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "estimated": est,
                    "cost_quota": int(cost) if cost else None,
                    "duration_ms": duration,
                    "first_token_ms": ttft,
                    "status": status,
                    "http_status": None if abort else getattr(live, "status", None),
                    "attempts": base["attempts"] or None,
                    "client_ua": base.get("client_ua"),
                    "error_code": "upstream_error" if error_chunk else None,
                    "error_message": mask_text(error_chunk) if error_chunk else None,
                }
            )
            spawn(_safe_stream_close(stream_ctx))
            on_finish()

    return StreamingResponse(
        gen(),
        status_code=200,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
