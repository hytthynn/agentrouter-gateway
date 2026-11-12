"""Management API клиент AgentRouter (new-api форк): заголовки §2.2, мягкая валидация §2.6."""

from typing import Any

from gateway_app.core.errors import NotFoundError, ProviderAuthError, ProviderError
from gateway_app.core.logging import get_logger
from gateway_app.core.masking import mask_text
from gateway_app.providers.agentrouter import transport
from gateway_app.providers.base import (
    CheckinOutcome,
    CreateTokenSpec,
    PlatformStatus,
    ProviderCredentials,
    ProviderTokenInfo,
    UserSelf,
)

log = get_logger(__name__)

# Браузерный SPA-профиль для консольных запросов (§10.2 — «работает»)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CHECKIN_CANDIDATES = ["/api/user/checkin", "/api/user/check_in", "/api/user/sign_in"]


def _headers(creds: ProviderCredentials, *, xhr: bool = True) -> dict[str, str]:
    h = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {creds.system_token}",
        "Content-Type": "application/json",
        "Referer": creds.base_url + "/console",
        "Origin": creds.base_url,
    }
    if creds.provider_user_id is not None:
        h["New-Api-User"] = str(creds.provider_user_id)
    if xhr:
        h["X-Requested-With"] = "XMLHttpRequest"
    return h


async def _call(
    creds: ProviderCredentials,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Консольный вызов с проверкой конверта {"success", "message", "data"} (§2.6)."""
    url = creds.base_url.rstrip("/") + path
    status, body, text = await transport.request_json(
        method, url, headers=_headers(creds), json_body=json_body, timeout_s=timeout_s
    )
    if transport.looks_like_waf(status, text):
        raise ProviderError(f"WAF-блок на консольном запросе {path} (HTTP {status})")
    if status == 401:
        raise ProviderAuthError(mask_text(f"Консольный API 401 {path}: {text[:200]}"))
    if status >= 400:
        raise ProviderError(f"Консольный API {method} {path} → HTTP {status}: {mask_text(text[:200])}")
    if body is None:
        raise ProviderError(f"Консольный API {path}: тело не JSON")
    # Конверт new-api; для нестандартных ответов возвращаем как есть
    success = body.get("success", None)
    if success is False:
        message = mask_text(str(body.get("message") or ""))
        raise ProviderError(f"Провайдер вернул ошибку ({path}): {message}")
    return body


def _extract_data(body: dict[str, Any]) -> Any:
    return body.get("data")


# --------------------------------------------------------------- эндпоинты

async def get_user_self(creds: ProviderCredentials) -> UserSelf:
    body = await _call(creds, "GET", "/api/user/self")
    data = _extract_data(body) or {}
    if not isinstance(data, dict):
        raise ProviderError("/api/user/self: data не объект")
    return UserSelf(
        id=_as_int(data.get("id")),
        username=data.get("username"),
        quota=_as_int(data.get("quota")),
        used_quota=_as_int(data.get("used_quota")),
        request_count=_as_int(data.get("request_count")),
        group=data.get("group"),
        raw=data,
    )


async def list_models(creds: ProviderCredentials) -> list[str]:
    """GET /api/models с фолбэком /api/user/models (§2.2)."""
    for path in ("/api/models", "/api/user/models"):
        try:
            body = await _call(creds, "GET", path)
        except NotFoundError:
            continue
        except ProviderError as exc:
            log.debug("list_models %s не удался: %s", path, exc)
            continue
        data = _extract_data(body)
        if isinstance(data, dict):
            data = data.get("models") or data.get("data") or []
        if isinstance(data, list):
            return [str(m) for m in data]
    raise ProviderError("Не удалось получить список моделей (/api/models и фолбэк)")


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _norm_key(raw_key: str | None) -> str | None:
    if not raw_key:
        return None
    return raw_key if raw_key.startswith("sk-") else f"sk-{raw_key}"


def _token_from(data: dict[str, Any]) -> ProviderTokenInfo:
    limits_raw = data.get("model_limits")
    if isinstance(limits_raw, str):
        limits = [s.strip() for s in limits_raw.split(",") if s.strip()] or None
    elif isinstance(limits_raw, list):
        limits = [str(s) for s in limits_raw] or None
    else:
        limits = None
    return ProviderTokenInfo(
        provider_token_id=_as_int(data.get("id")),
        name=data.get("name"),
        key_full=_norm_key(data.get("key")),
        status=_as_int(data.get("status")),
        unlimited_quota=bool(data.get("unlimited_quota", True)),
        remain_quota=_as_int(data.get("remain_quota")),
        used_quota=_as_int(data.get("used_quota")),
        expired_time=_as_int(data.get("expired_time")) if data.get("expired_time") is not None else -1,
        model_limits_enabled=bool(data.get("model_limits_enabled", False)),
        model_limits=limits,
    )


async def list_tokens(creds: ProviderCredentials, page_size: int = 100) -> list[ProviderTokenInfo]:
    """GET /api/token/?p=0&size=N — принимает список или объект с items (runtime-discovery)."""
    out: list[ProviderTokenInfo] = []
    page = 0
    while True:
        body = await _call(creds, "GET", f"/api/token/?p={page}&size={page_size}")
        data = _extract_data(body)
        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items") or data.get("records") or []
        for item in items:
            if isinstance(item, dict):
                out.append(_token_from(item))
        if len(items) < page_size:
            break
        page += 1
        if page > 50:  # страховка от зацикливания
            break
    return out


async def create_token(creds: ProviderCredentials, spec: CreateTokenSpec) -> ProviderTokenInfo:
    body = await _call(
        creds,
        "POST",
        "/api/token/",
        json_body={
            "name": spec.name,
            "unlimited_quota": spec.unlimited_quota,
            "remain_quota": spec.remain_quota,
            "expired_time": spec.expired_time,
            "model_limits_enabled": spec.model_limits_enabled,
            "model_limits": ",".join(spec.model_limits or []),
            "group": spec.group,
        },
    )
    data = _extract_data(body) or {}
    info = _token_from(data) if isinstance(data, dict) else ProviderTokenInfo(provider_token_id=None)
    if not info.key_full:
        raise ProviderError("Создание ключа: провайдер не вернул полный key")
    return info


async def update_token(
    creds: ProviderCredentials, token_id: int, fields: dict[str, Any]
) -> ProviderTokenInfo:
    """PUT /api/token/ — требует полный объект: сначала берём список, накладываем поля."""
    tokens = await list_tokens(creds)
    current = next((t for t in tokens if t.provider_token_id == token_id), None)
    if current is None:
        raise NotFoundError(f"Ключ {token_id} не найден у провайдера")
    payload: dict[str, Any] = {
        "id": current.provider_token_id,
        "name": current.name,
        "unlimited_quota": current.unlimited_quota,
        "remain_quota": current.remain_quota,
        "expired_time": current.expired_time,
        "model_limits_enabled": current.model_limits_enabled,
        "model_limits": ",".join(current.model_limits or []),
        "status": current.status if current.status is not None else 1,
    }
    payload.update(fields)
    body = await _call(creds, "PUT", "/api/token/", json_body=payload)
    data = _extract_data(body)
    return _token_from(data) if isinstance(data, dict) and data.get("key") else current


async def delete_token(creds: ProviderCredentials, token_id: int) -> None:
    await _call(creds, "DELETE", f"/api/token/{token_id}")


_ALREADY_MARKERS = (
    "already",
    "已签",
    "签到过",
    "重复",
    "duplicate",
    "today",
    "signed in",
)


def _looks_already_done(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in _ALREADY_MARKERS)


async def checkin(creds: ProviderCredentials, cached_path: str | None = None) -> CheckinOutcome:
    """POST чек-ин: перебор кандидатов (§2.6), «уже чекинились» — не ошибка (§2.3)."""
    candidates = ([cached_path] if cached_path else []) + [
        p for p in CHECKIN_CANDIDATES if p != cached_path
    ]
    last_msg = ""
    for path in candidates:
        url = creds.base_url.rstrip("/") + path
        status, body, text = await transport.request_json(
            "POST", url, headers=_headers(creds), json_body={}, timeout_s=30.0
        )
        if transport.looks_like_waf(status, text):
            raise ProviderError(f"WAF-блок на чек-ине ({path})")
        if status == 404:
            last_msg = f"{path}: 404"
            continue
        if status == 401:
            raise ProviderAuthError("Чек-ин: токен отклонён (401)")
        if body is None:
            last_msg = f"{path}: тело не JSON"
            continue
        success = bool(body.get("success"))
        message = mask_text(str(body.get("message") or ""))
        if success:
            return CheckinOutcome(True, False, message, path=path, raw=body)
        if _looks_already_done(message):
            return CheckinOutcome(
                False, True, message or "уже чекинились сегодня", path=path, raw=body
            )
        last_msg = f"{path}: {message or text[:200]}"
        # success=false без маркера «уже» — пробуем следующий кандидат
    raise ProviderError(f"Чек-ин не удался: {last_msg or 'все кандидаты исчерпаны'}")


async def platform_status(base_url: str) -> PlatformStatus:
    """Публичный GET /api/status — достижимость + версия платформы."""
    url = base_url.rstrip("/") + "/api/status"
    status, body, _text = await transport.request_json(
        "GET", url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"}, timeout_s=15.0
    )
    if status >= 400 or body is None:
        raise ProviderError(f"/api/status: HTTP {status}")
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return PlatformStatus(
        version=data.get("version"),
        system_name=data.get("system_name"),
        quota_per_unit=_as_int(data.get("quota_per_unit")),
        turnstile_check=bool(data.get("turnstile_check", False)),
        raw=data,
    )
