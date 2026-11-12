"""GitHub OAuth login flow (§9.2): Camoufox + pyotp, headless, с ретраями и скриншотами.

Точки входа: `github_oauth_login()` — полный цикл; результат — провайдерский user_id,
session-cookie AgentRouter и storage-state для последующих перелогинов.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

import pyotp

from gateway_app.core.errors import AutomationError
from gateway_app.core.logging import get_logger
from gateway_app.core.masking import mask_text
from gateway_app.providers.agentrouter.adapter import QUOTA_PER_UNIT  # noqa: F401 (константа домена)

log = get_logger(__name__)

GITHUB_CLIENT_ID = "Ov23lidtiR4LeVZvVRNL"  # §2.1
STEP_TIMEOUT_MS = 30_000  # §9.3
FLOW_RETRIES = 2

# Тексты, означающие «GitHub отклонил вход» (§9.3)
_REJECT_MARKERS = (
    "incorrect username or password",
    "invalid username or password",
    "account has been locked",
    "suspicious login",
    "too many attempts",
)


class FlowContext(Protocol):
    """Что flow ожидает от задачи: логи, шаги, ожидание кода от пользователя."""

    def log(self, line: str) -> None: ...
    def step_status(self, key: str, status: str) -> None: ...
    async def wait_user_code(self, timeout_s: float = 300.0) -> str: ...


async def github_oauth_login(
    *,
    ctx: FlowContext,
    base_url: str,
    github_login: str,
    github_password: str,
    totp_secret: str,
    storage_state: dict | None,
    screenshots_dir: Path,
) -> dict[str, Any]:
    """Полный OAuth-вход: AgentRouter → GitHub (2FA) → обратно. Ретрай всего flow ×2 (§9.3)."""
    last_error: Exception | None = None
    for attempt in range(1, FLOW_RETRIES + 1):
        try:
            return await _single_flow(
                ctx=ctx,
                base_url=base_url,
                github_login=github_login,
                github_password=github_password,
                totp_secret=totp_secret,
                storage_state=storage_state if attempt == 1 else None,
                screenshots_dir=screenshots_dir,
            )
        except AutomationError as exc:
            last_error = exc
            ctx.log(f"⚠ Попытка {attempt}/{FLOW_RETRIES} не удалась: {exc}")
    raise AutomationError(f"OAuth-вход не удался после {FLOW_RETRIES} попыток: {last_error}")


async def _single_flow(**kwargs: Any) -> dict[str, Any]:
    from gateway_app.automation import browser

    ctx: FlowContext = kwargs["ctx"]
    base_url: str = kwargs["base_url"].rstrip("/")
    screenshots_dir: Path = kwargs["screenshots_dir"]
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    ctx.step_status("oauth", "running")
    ctx.log("▶ Запуск headless-браузера (Camoufox)")
    context = await browser.launch_context(kwargs["storage_state"])
    page = None
    try:
        page = await context.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)

        # --- 1. Страница логина AgentRouter ---
        ctx.log(f"▶ Открытие {base_url}/login")
        try:
            await page.goto(f"{base_url}/login", wait_until="domcontentloaded")
        except Exception as exc:
            raise AutomationError(f"Не удалось открыть страницу логина провайдера: {exc}") from exc

        # --- 2. Клик «Sign in with GitHub» (fallback: прямой authorize-URL) ---
        github_url = await _start_github_oauth(ctx, page, base_url)
        ctx.log(f"▶ GitHub OAuth: {github_url[:80]}…")

        # --- 3. GitHub: вход + 2FA + (возможно) device-код ---
        await _github_auth(
            ctx,
            page,
            github_login=kwargs["github_login"],
            github_password=kwargs["github_password"],
            totp_secret=kwargs["totp_secret"],
        )

        # --- 4. Ожидание завершения OAuth на стороне SPA AgentRouter ---
        ctx.log("▶ Ожидание завершения OAuth на стороне провайдера…")
        session_cookie, provider_user_id, username = await _wait_provider_session(
            ctx, page, context, base_url
        )

        # --- 5-6. Чтение user_id, сохранение storage-state ---
        storage = await context.storage_state()
        ctx.step_status("oauth", "done")
        ctx.log(f"✓ OAuth завершён: user_id={provider_user_id} ({username})")
        return {
            "provider_user_id": provider_user_id,
            "username": username,
            "session_cookie": session_cookie,
            "storage_state": storage,
        }
    except AutomationError:
        await _save_screenshot(page, screenshots_dir)
        raise
    except Exception as exc:
        await _save_screenshot(page, screenshots_dir)
        raise AutomationError(f"Неожиданная ошибка flow: {mask_text(str(exc), 0, 0)[:300]}") from exc
    finally:
        await browser.close_context(context)


async def _save_screenshot(page: Any, screenshots_dir: Path) -> None:
    if page is None:
        return
    try:
        path = screenshots_dir / "last_error.png"
        await page.screenshot(path=str(path), full_page=False)
        log.info("Скриншот последнего состояния: %s", path)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------- шаги


async def _start_github_oauth(ctx: FlowContext, page: Any, base_url: str) -> str:
    """Клик по кнопке GitHub; если не нашли — прямой URL через /api/oauth/state (§9.2)."""
    selectors = [
        'a[href*="github.com/login/oauth"]',
        'button:has-text("GitHub")',
        'a:has-text("GitHub")',
        'text=/sign in with github/i',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=8000)
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await el.click()
            return page.url
        except Exception:  # noqa: BLE001 — пробуем следующий селектор
            continue

    ctx.log("Кнопка GitHub не найдена — fallback: прямой OAuth-URL через /api/oauth/state")
    try:
        resp = await page.request.get(f"{base_url}/api/oauth/state")
        data = await resp.json() if resp else {}
        state = (data.get("data") or "") if isinstance(data, dict) else ""
        if not state:
            raise AutomationError("Провайдер не вернул oauth state")
        url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={GITHUB_CLIENT_ID}&state={state}&scope=user:email"
        )
        await page.goto(url, wait_until="domcontentloaded")
        return page.url
    except AutomationError:
        raise
    except Exception as exc:
        raise AutomationError(f"Не удалось начать GitHub OAuth: {exc}") from exc


async def _github_auth(
    ctx: FlowContext, page: Any, *, github_login: str, github_password: str, totp_secret: str
) -> None:
    """Логин → пароль → TOTP → (device verification) → authorize-страница."""
    try:
        await page.wait_for_url("**github.com**", timeout=STEP_TIMEOUT_MS)
    except Exception as exc:
        raise AutomationError(f"Редирект на GitHub не произошёл: {page.url}") from exc

    # Уже залогинены (storage-state)? Тогда сразу authorize.
    if "github.com/login" not in page.url:
        ctx.log("✓ GitHub: сессия активна (storage-state)")
        await _maybe_authorize(ctx, page)
        return

    ctx.log("▶ Ввод учётных данных GitHub")
    try:
        await page.fill("#login_field", github_login, timeout=STEP_TIMEOUT_MS)
        await page.fill("#password", github_password, timeout=STEP_TIMEOUT_MS)
        async with page.expect_navigation(wait_until="domcontentloaded"):
            await page.click('input[name="commit"]')
    except Exception as exc:
        await _detect_rejection(page)
        raise AutomationError(f"Не удалось отправить форму входа GitHub: {exc}") from exc

    await _detect_rejection(page)

    # TOTP-страница
    if await _page_has(page, "#app_totp"):
        ctx.log("▶ Страница 2FA — генерация TOTP")
        await _submit_totp(ctx, page, totp_secret)

    # Device verification (код из письма, §9.5)
    if "verified-device" in page.url or await _page_has(page, 'input[name="otp"]'):
        code = await ctx.wait_user_code()
        ctx.log("▶ Ввод device-verification кода")
        try:
            await page.fill('input[name="otp"]', code, timeout=STEP_TIMEOUT_MS)
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click('button[type="submit"], input[type="submit"]')
        except Exception as exc:
            raise AutomationError(f"Не удалось отправить код верификации: {exc}") from exc
        if await _page_has(page, "#app_totp"):
            await _submit_totp(ctx, page, totp_secret)

    await _maybe_authorize(ctx, page)
    await _detect_rejection(page)


async def _submit_totp(ctx: FlowContext, page: Any, totp_secret: str) -> None:
    """TOTP с ретраями ±1 интервал (риск тайм-дрифта, §16)."""
    totp = pyotp.TOTP(totp_secret.replace(" ", ""))
    for offset in (0, 1, -1):
        code = totp.now() if offset == 0 else totp.at(totp.time() + offset * 30)
        try:
            await page.fill("#app_totp", code, timeout=STEP_TIMEOUT_MS)
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click('button[type="submit"], input[type="submit"]')
        except Exception as exc:
            raise AutomationError(f"Не удалось отправить TOTP: {exc}") from exc
        await _detect_rejection(page)
        if await _page_has(page, "#app_totp"):
            ctx.log(f"TOTP отклонён (offset {offset:+d}) — ретрай")
            continue
        return
    raise AutomationError("TOTP отклонён GitHub после ретраев (проверьте секрет)")


async def _maybe_authorize(ctx: FlowContext, page: Any) -> None:
    """Кнопка «Authorize …» на странице согласия (если показывается)."""
    try:
        btn = page.locator('button:has-text("Authorize"), button:has-text("authoriz")').first
        await btn.wait_for(state="visible", timeout=6000)
        async with page.expect_navigation(wait_until="domcontentloaded"):
            await btn.click()
        ctx.log("✓ Кнопка Authorize нажата")
    except Exception:  # noqa: BLE001 — страницы authorize может не быть
        pass


async def _wait_provider_session(ctx: FlowContext, page: Any, context: Any, base_url: str):
    """Ожидание cookie `session` + localStorage user на домене провайдера (§9.2 шаг 5)."""
    deadline = asyncio.get_event_loop().time() + STEP_TIMEOUT_MS / 1000
    while asyncio.get_event_loop().time() < deadline:
        if page.url.startswith(base_url):
            cookies = await context.cookies(base_url)
            session_cookie = next((c["value"] for c in cookies if c.get("name") == "session"), None)
            if session_cookie:
                user_raw = await page.evaluate("() => localStorage.getItem('user')")
                user = {}
                if user_raw:
                    try:
                        user = json.loads(user_raw)
                    except json.JSONDecodeError:
                        user = {}
                provider_user_id = user.get("id")
                username = user.get("username")
                if provider_user_id:
                    return session_cookie, int(provider_user_id), str(username or "")
        await asyncio.sleep(1.0)
    raise AutomationError(
        f"Сессия провайдера не установлена за 30 с (последний URL: {page.url[:120]})"
    )


async def _page_has(page: Any, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=1500)
    except Exception:  # noqa: BLE001
        return False


async def _detect_rejection(page: Any) -> None:
    """«GitHub отклонил аккаунт» → типизированная ошибка (§9.3)."""
    try:
        flash = await page.locator(".flash-error, #js-flash-container, .js-login-error").first
        if await flash.is_visible(timeout=800):
            text = (await flash.text_content() or "").lower()
            if any(m in text for m in _REJECT_MARKERS):
                raise AutomationError(f"GitHub отклонил вход: {text[:200]}")
    except AutomationError:
        raise
    except Exception:  # noqa: BLE001
        pass
