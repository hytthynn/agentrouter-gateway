"""Диагностика подключения аккаунта (§8.6): token, транспорт, per-profile верdict, модели."""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.core.security import SecretBox
from gateway_app.domain.accounts.base import creds_of, get_account
from gateway_app.domain.keys.provider_keys import pick_active_key_for_account
from gateway_app.providers.agentrouter import transport


async def diagnose_account(
    session: AsyncSession, adapter: Any, box: SecretBox, account_id: int
) -> dict[str, Any]:
    """Последовательные проверки → карточка с зелёными/красными пунктами."""
    account = await get_account(session, account_id)
    creds = creds_of(box, account)
    checks: list[dict[str, Any]] = []

    # 1. System token → /api/user/self
    try:
        info = await adapter.get_user_self(creds)
        ok = info.id is None or account.provider_user_id is None or info.id == account.provider_user_id
        checks.append(
            {
                "key": "system_token",
                "ok": ok,
                "detail": (
                    f"user_id={info.id}, баланс={info.quota}, группа={info.group or 'default'}"
                    + ("" if ok else " — user_id не совпадает с сохранённым")
                ),
            }
        )
    except Exception as exc:
        checks.append({"key": "system_token", "ok": False, "detail": str(exc)[:300]})

    # 2. Ключ провайдера → /v1/models апстрима (WAF-вердикт, транспорт)
    api_key: str | None = None
    try:
        _, api_key = await pick_active_key_for_account(session, box, account.id or 0)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        url = account.base_url.rstrip("/") + "/v1/models"
        status, body, text = await transport.request_json("GET", url, headers=headers, timeout_s=20)
        if 200 <= status < 300 and isinstance(body, dict):
            models = body.get("data") or []
            checks.append(
                {"key": "upstream_models", "ok": True, "detail": f"HTTP {status}, моделей: {len(models)}"}
            )
        else:
            waf = transport.looks_like_waf(status, text)
            checks.append(
                {
                    "key": "upstream_models",
                    "ok": False,
                    "detail": f"HTTP {status}" + (" — похоже на WAF-блок" if waf else f": {text[:150]}"),
                }
            )
    except Exception as exc:
        checks.append({"key": "upstream_models", "ok": False, "detail": str(exc)[:300]})

    # 3. Каждый client-профиль — пробный запрос (§10.3: per-profile верdict)
    for profile in (await _profiles(session)):
        verdict, detail = await _profile_verdict(
            account.base_url, api_key, profile["name"], profile["user_agent"], profile["extra_headers"] or {}
        )
        checks.append({"key": f"profile:{profile['name']}", "ok": verdict == "ok", "detail": f"{verdict}: {detail}"})

    # 4. Модели аккаунта
    try:
        models = await adapter.list_models(creds)
        checks.append({"key": "models_list", "ok": True, "detail": f"доступно моделей: {len(models)}"})
    except Exception as exc:
        checks.append({"key": "models_list", "ok": False, "detail": str(exc)[:300]})

    return {"account_id": account_id, "checks": checks}


async def _profiles(session: AsyncSession) -> list[dict[str, Any]]:
    from gateway_app.providers.agentrouter.profiles import list_profiles

    rows = await list_profiles(session)
    return [
        {"name": p.name, "user_agent": p.user_agent, "extra_headers": p.extra_headers}
        for p in rows
        if p.name != "browser"
    ]


async def _profile_verdict(
    base_url: str, api_key: str | None, name: str, ua: str, extra: dict[str, str]
) -> tuple[str, str]:
    """ok / waf_block / auth_error для одного профиля (GET /v1/models — без расхода токенов)."""
    if not api_key:
        return "skip", "нет ключа провайдера"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": ua, **extra}
    try:
        status, _body, text = await transport.request_json(
            "GET", base_url.rstrip("/") + "/v1/models", headers=headers, timeout_s=20
        )
    except Exception as exc:
        return "error", str(exc)[:200]
    if 200 <= status < 300:
        return "ok", f"HTTP {status}"
    if transport.looks_like_waf(status, text) or (status == 401 and "invalid token" not in text.lower()):
        return "waf_block", f"HTTP {status}"
    if status == 401:
        return "auth_error", "HTTP 401"
    return "error", f"HTTP {status}: {text[:150]}"


__all__ = ["diagnose_account"]
