"""Camoufox headless-браузер (анти-детект Firefox, Playwright API) — §9.1.

Импорты ленивые: приложение работает без установленного браузера
(в этом случае функции автоматизации возвращают понятную ошибку).
"""

import contextlib
from typing import Any

from gateway_app.core.errors import AutomationError
from gateway_app.core.logging import get_logger

log = get_logger(__name__)

# Отпечаток зафиксирован на macOS-подобный (§9.1 — снижает риск device-OTP)
CAMOUFOX_OPTS: dict[str, Any] = {
    "headless": True,
    "humanize": True,
    "locale": "en-US",
    "os": "macos",
}


def _check_available() -> None:
    try:
        import camoufox.async_api  # noqa: F401
    except ImportError as exc:
        raise AutomationError(
            "Camoufox не установлен: выполните `uv sync` и `uv run camoufox fetch` "
            "(загрузка браузера ~120 МБ)"
        ) from exc


async def launch_context(storage_state: dict | None = None):
    """Открыть браузер + контекст (с сохранённым storage-state, если есть). Возвращает context.

    Вызывающий обязан `await context.close()` / `await context.browser.close()`.
    """
    _check_available()
    from camoufox.async_api import AsyncCamoufox

    opts = dict(CAMOUFOX_OPTS)
    if storage_state:
        opts["storage_state"] = storage_state
    try:
        browser = await AsyncCamoufox(**opts).__aenter__()
        context = await browser.new_context(locale="en-US")
        context._agw_browser = browser  # для закрытия владельцем
        return context
    except Exception as exc:
        raise AutomationError(f"Не удалось запустить Camoufox: {exc}") from exc


async def close_context(context: Any) -> None:
    browser = getattr(context, "_agw_browser", None)
    with contextlib.suppress(Exception):
        await context.close()
    if browser is not None:
        with contextlib.suppress(Exception):
            await browser.close()
