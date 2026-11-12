"""Тесты шифрования и маскирования (§5.2, FR-7.4)."""

import pytest

from gateway_app.core.masking import mask_text, truncate
from gateway_app.core.security import (
    CryptoError,
    SecretBox,
    load_or_create_master_key,
    mask_secret,
)


def test_encrypt_decrypt_roundtrip():
    box = SecretBox(b"\x01" * 32)
    blob = box.encrypt("github_password_enc", "s3cret!")
    assert blob.startswith(b"v1:")
    assert box.decrypt("github_password_enc", blob) == "s3cret!"


def test_aad_binds_field_name():
    box = SecretBox(b"\x01" * 32)
    blob = box.encrypt("system_token_enc", "tok")
    with pytest.raises(CryptoError):
        box.decrypt("totp_secret_enc", blob)  # подмена поля невозможна


def test_wrong_master_key_fails():
    blob = SecretBox(b"\x01" * 32).encrypt("key_enc", "sk-x")
    with pytest.raises(CryptoError):
        SecretBox(b"\x02" * 32).decrypt("key_enc", blob)


def test_master_key_file_created(tmp_path):
    kf = tmp_path / ".master_key"
    key1 = load_or_create_master_key(kf, None)
    assert len(key1) == 32
    key2 = load_or_create_master_key(kf, None)
    assert key1 == key2  # переиспользуется
    assert kf.exists()


def test_master_key_from_env():
    key = load_or_create_master_key(_tmp_path_unused := __import__("pathlib").Path("/nonexistent"), "abcd" * 8)
    assert len(key) == 32


def test_gateway_key_format():
    key = SecretBox.generate_gateway_key()
    assert key.startswith("agw-")
    body = key[4:]
    assert len(body) == 40
    assert body.isalnum()


def test_key_hash_stable():
    assert SecretBox.key_hash("abc") == SecretBox.key_hash("abc")
    assert SecretBox.key_hash("abc") != SecretBox.key_hash("abd")


def test_mask_text_hides_keys():
    text = "Ошибка: ключ sk-AbcDef1234567890 недействителен, agw-XyZ123456789012345678 тоже"
    masked = mask_text(text)
    assert "sk-AbcDef" not in masked
    assert "agw-XyZ1234567890" not in masked
    assert "sk-***" in masked and "agw-***" in masked


def test_mask_secret_display():
    assert mask_secret("sk-abcdef123456", 3, 4) == "sk-…3456"


def test_truncate():
    assert truncate("x" * 3000, 2048).endswith("…")
    assert len(truncate("x" * 3000, 2048)) == 2048
