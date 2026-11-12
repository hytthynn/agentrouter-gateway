"""Шифрование секретов: AES-256-GCM, формат `v1:<nonce_b64>:<ciphertext_b64>`, AAD = имя поля."""

import base64
import hashlib
import os
import secrets
import string
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = "v1"
_B62_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits


class CryptoError(Exception):
    """Ошибка шифрования/расшифровки (повреждённый блок или неверный мастер-ключ)."""


def _decode_material(raw: str) -> bytes:
    """Мастер-ключ из строки: base32/base64/hex — либо sha256 произвольной строки."""
    raw = raw.strip()
    cleaned = raw.replace("-", "").replace("=", "")
    for decoder in (_try_base32, _try_base64, _try_hex):
        data = decoder(cleaned)
        if data:
            return data
    return hashlib.sha256(raw.encode()).digest()  # произвольная строка


def _try_base32(s: str) -> bytes | None:
    import base64 as b64

    if len(s) < 48 or not all(c in string.ascii_uppercase + "234567" for c in s[:48]):
        return None
    try:
        return b64.b32decode(s + "=" * (-len(s) % 8))
    except Exception:
        return None


def _try_base64(s: str) -> bytes | None:
    import base64 as b64

    try:
        data = b64.b64decode(s, validate=True)
        return data if len(data) == 32 else None
    except Exception:
        return None


def _try_hex(s: str) -> bytes | None:
    if len(s) != 64 or not all(c in string.hexdigits for c in s):
        return None
    return bytes.fromhex(s)


def load_or_create_master_key(key_file: Path, env_key: str | None) -> bytes:
    """Мастер-ключ: env AGW_MASTER_KEY либо файл data/.master_key (32 байта, права 0600)."""
    if env_key:
        return _decode_material(env_key)
    if key_file.exists():
        data = key_file.read_bytes().strip()
        if len(data) == 32:
            return data
        return _decode_material(data.decode("utf-8", errors="strict"))
    key = os.urandom(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass  # Windows: права ограничены ACL файловой системы
    return key


class SecretBox:
    """Шифрование/расшифровка секретных полей БД. AAD = имя поля (защита от подмены)."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise CryptoError("Мастер-ключ должен быть 32 байта")
        self._aes = AESGCM(master_key)

    def encrypt(self, field: str, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), field.encode("utf-8"))
        return f"{FORMAT_VERSION}:{base64.b64encode(nonce).decode()}:{base64.b64encode(ct).decode()}".encode()

    def decrypt(self, field: str, blob: bytes | None) -> str | None:
        if blob is None:
            return None
        try:
            parts = bytes(blob).decode("ascii").split(":")
            if len(parts) != 3 or parts[0] != FORMAT_VERSION:
                raise CryptoError(f"Неизвестный формат блоба поля {field!r}")
            nonce = base64.b64decode(parts[1])
            ct = base64.b64decode(parts[2])
            return self._aes.decrypt(nonce, ct, field.encode("utf-8")).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise CryptoError(
                f"Не удалось расшифровать поле {field!r}: мастер-ключ не совпадает или данные повреждены"
            ) from exc

    # ---- утилиты ключей ----

    @staticmethod
    def key_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_gateway_key() -> str:
        """Ключ шлюза: `agw-<base62-40>`."""
        rng = secrets.SystemRandom()
        suffix = "".join(rng.choice(_B62_ALPHABET) for _ in range(40))
        return f"agw-{suffix}"


def mask_secret(value: str, keep_start: int = 3, keep_end: int = 4) -> str:
    """Маскирование ключа для отображения: `sk-abc…wxyz`."""
    if not value:
        return ""
    if len(value) <= keep_start + keep_end + 2:
        return value[:keep_start] + "…" + value[-keep_end:]
    return f"{value[:keep_start]}…{value[-keep_end:]}"
