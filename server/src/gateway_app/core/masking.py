"""Маскирование секретов в логах и ошибках (FR-7.4)."""

import re

_PATTERNS = [
    # ключи провайдера и шлюза
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"\bagw-[A-Za-z0-9_-]{8,}"), "agw-***"),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
    # New-Api-User: не секрет, но токен в заголовке может прилететь в трейсе
    (re.compile(r"(?i)(new-api-user\s*[:=]\s*)(\d{3,})"), r"\1***"),
    # длинные hex/base64-подобные последовательности (сессионные cookie)
    (re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE), "***"),
]


def mask_text(text: str) -> str:
    """Заменяет все узнаваемые секреты на *** (для системных логов и сообщений об ошибках)."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def truncate(text: str, limit: int = 2048) -> str:
    """Обрезка длинных сообщений (request_logs.error_message — 2 KiB)."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
