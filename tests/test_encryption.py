"""加密模块测试。"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.fernet import Fernet

from storage.encryption import SecureVault
from storage.exceptions import EncryptionError


def test_encrypt_decrypt(temp_dir):
    vault = SecureVault(temp_dir / "data")
    plaintext = "sk-my-secret-api-key"
    encrypted = vault.encrypt(plaintext)
    assert encrypted.startswith("v2:")
    assert encrypted != plaintext
    decrypted = vault.decrypt(encrypted)
    assert decrypted == plaintext


def test_encrypt_non_string_raises(temp_dir):
    vault = SecureVault(temp_dir / "data")
    with pytest.raises(EncryptionError):
        vault.encrypt(12345)  # type: ignore


def test_decrypt_invalid_token(temp_dir):
    vault = SecureVault(temp_dir / "data")
    with pytest.raises(EncryptionError):
        vault.decrypt("not-a-valid-token")


def test_key_deterministic(temp_dir):
    """同一目录应生成相同主密钥。"""
    data_dir = temp_dir / "data"
    vault1 = SecureVault(data_dir)
    encrypted = vault1.encrypt("hello")

    vault2 = SecureVault(data_dir)
    assert vault2.decrypt(encrypted) == "hello"


def test_corrupted_master_key_has_clear_error(temp_dir) -> None:
    data_dir = temp_dir / "data"
    data_dir.mkdir()
    (data_dir / ".master_key").write_text("damaged", encoding="utf-8")

    with pytest.raises(EncryptionError, match="主密钥"):
        SecureVault(data_dir).encrypt("secret")


def test_working_keyring_does_not_create_local_master_file(temp_dir, monkeypatch) -> None:
    values = {}

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            return values.get((service, username))

        @staticmethod
        def set_password(service, username, value):
            values[(service, username)] = value

    monkeypatch.setattr(SecureVault, "_get_keyring", lambda self: FakeKeyring)
    data_dir = temp_dir / "data"

    assert SecureVault(data_dir).decrypt(SecureVault(data_dir).encrypt("hello")) == "hello"
    assert not (data_dir / ".master_key").exists()


def test_local_master_file_is_password_protected_v2(temp_dir) -> None:
    data_dir = temp_dir / "data"
    SecureVault(data_dir).encrypt("hello")

    lines = (data_dir / ".master_key").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0] == "v2"


def test_missing_local_master_password_has_clear_error(temp_dir, monkeypatch) -> None:
    monkeypatch.delenv("LOCALAI_MASTER_PASSWORD", raising=False)

    with pytest.raises(EncryptionError, match="LOCALAI_MASTER_PASSWORD"):
        SecureVault(temp_dir / "data").encrypt("secret")


def test_legacy_master_file_is_migrated_to_password_protected_format(temp_dir) -> None:
    data_dir = temp_dir / "data"
    data_dir.mkdir()
    vault = SecureVault(data_dir)
    legacy_key = Fernet.generate_key()
    salt = os.urandom(16)
    encrypted_key = Fernet(
        vault._derive_key(f"{vault.APP_NAME}-v1", salt)
    ).encrypt(legacy_key)
    (data_dir / ".master_key").write_text(
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}\n"
        f"{base64.urlsafe_b64encode(encrypted_key).decode('ascii')}\n",
        encoding="utf-8",
    )

    assert SecureVault(data_dir).key == legacy_key
    assert (data_dir / ".master_key").read_text(encoding="utf-8").startswith("v2\n")


def test_password_provider_knows_whether_password_is_being_created(
    temp_dir, monkeypatch
) -> None:
    monkeypatch.delenv("LOCALAI_MASTER_PASSWORD", raising=False)
    calls = []
    data_dir = temp_dir / "data"

    first = SecureVault(
        data_dir,
        password_provider=lambda creating: calls.append(creating) or "strong-password-1",
    )
    ciphertext = first.encrypt("secret")
    second = SecureVault(
        data_dir,
        password_provider=lambda creating: calls.append(creating) or "strong-password-1",
    )

    assert second.decrypt(ciphertext) == "secret"
    assert calls == [True, False]
