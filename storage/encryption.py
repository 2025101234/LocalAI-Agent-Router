"""本地密钥安全管理：AES-256-GCM 加密 + 可选 keyring 存储。"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from loguru import logger

from storage.exceptions import EncryptionError
from storage.permissions import atomic_write_text, secure_directory, secure_file


class SecureVault:
    """AES-256-GCM 加密保险库，用于加密 API Key 等敏感信息。

    主密钥优先仅存放在平台凭据管理器（keyring）。若不可用，则要求
    用户提供主密码，再将主密钥保存到权限受限的本地文件。
    """

    APP_NAME: str = "LocalAIAgentRouter"
    KEY_FILENAME: str = ".master_key"
    KEY_FILE_VERSION: str = "v2"
    MIN_PASSWORD_LENGTH: int = 12

    def __init__(
        self,
        data_dir: Path,
        master_password: str | None = None,
        password_provider: Callable[[bool], str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        secure_directory(self.data_dir)
        self._master_password = master_password
        self._password_provider = password_provider
        self._key: bytes | None = None

    def _get_keyring(self) -> object | None:
        try:
            import keyring
            return keyring
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - keyring 可选
            logger.debug(f"keyring 不可用: {exc}")
            return None

    def _load_key_from_keyring(self) -> bytes | None:
        keyring = self._get_keyring()
        if keyring is None:
            return None
        try:
            value = keyring.get_password(self.APP_NAME, "master_key")
            if value:
                return base64.urlsafe_b64decode(value)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning(f"从 keyring 读取主密钥失败: {exc}")
        return None

    def _save_key_to_keyring(self, key: bytes) -> bool:
        keyring = self._get_keyring()
        if keyring is None:
            return False
        try:
            encoded = base64.urlsafe_b64encode(key).decode("ascii")
            keyring.set_password(self.APP_NAME, "master_key", encoded)
            if keyring.get_password(self.APP_NAME, "master_key") != encoded:
                raise RuntimeError("keyring 写入后校验失败")
            logger.info("主密钥已保存到 keyring")
            return True
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning(f"保存主密钥到 keyring 失败: {exc}")
            return False

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480_000,
            backend=default_backend(),
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

    def _load_or_create_key(self) -> bytes:
        key = self._load_key_from_keyring()
        if key is not None:
            logger.debug("已从 keyring 加载主密钥")
            return key

        key_file = self.data_dir / self.KEY_FILENAME
        if key_file.exists():
            key, legacy = self._load_key_file(key_file)
            if self._save_key_to_keyring(key):
                key_file.unlink()
                logger.info("本地主密钥已安全迁移到 keyring")
            elif legacy:
                self._write_key_file(
                    key_file, key, self._get_master_password(creating=True)
                )
                logger.info("旧版本地主密钥已升级为主密码保护格式")
            return key

        key = Fernet.generate_key()
        if self._save_key_to_keyring(key):
            logger.info("已生成新的 keyring 主密钥")
            return key
        self._write_key_file(key_file, key, self._get_master_password(creating=True))
        logger.info("已生成新的主密码保护本地主密钥")
        return key

    def _get_master_password(self, creating: bool = False) -> str:
        password = self._master_password or os.environ.get("LOCALAI_MASTER_PASSWORD")
        if not password and self._password_provider is not None:
            password = self._password_provider(creating)
        if not password:
            raise EncryptionError(
                "系统 keyring 不可用。请设置 LOCALAI_MASTER_PASSWORD，"
                "或在交互终端输入至少 12 位本地主密码"
            )
        if len(password) < self.MIN_PASSWORD_LENGTH:
            raise EncryptionError(
                f"本地主密码至少需要 {self.MIN_PASSWORD_LENGTH} 个字符"
            )
        self._master_password = password
        return password

    def _load_key_file(self, path: Path) -> tuple[bytes, bool]:
        try:
            secure_file(path)
            content = path.read_text(encoding="utf-8").strip().splitlines()
            legacy = len(content) == 2
            if legacy:
                salt_text, encrypted_text = content
                password = f"{self.APP_NAME}-v1"
            elif len(content) == 3 and content[0] == self.KEY_FILE_VERSION:
                _, salt_text, encrypted_text = content
                password = self._get_master_password()
            else:
                raise EncryptionError("主密钥文件格式错误")
            salt = base64.urlsafe_b64decode(salt_text)
            encrypted_key = base64.urlsafe_b64decode(encrypted_text)
            fernet = Fernet(self._derive_key(password, salt))
            return fernet.decrypt(encrypted_key), legacy
        except EncryptionError:
            raise
        except Exception as exc:
            raise EncryptionError(f"读取本地主密钥失败: {exc}") from exc

    def _write_key_file(self, path: Path, key: bytes, password: str) -> None:
        salt = os.urandom(16)
        encrypted_key = Fernet(self._derive_key(password, salt)).encrypt(key)
        content = "\n".join(
            (
                self.KEY_FILE_VERSION,
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(encrypted_key).decode("ascii"),
            )
        )
        atomic_write_text(path, content + "\n")

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = self._load_or_create_key()
        return self._key

    def unlock(self, password: str) -> None:
        """使用主密码解锁或初始化本地主密钥，不保留失败的密码。"""
        previous_password = self._master_password
        previous_key = self._key
        self._master_password = password
        self._key = None
        try:
            _ = self.key
        except Exception:
            self._master_password = previous_password
            self._key = previous_key
            raise

    def encrypt(self, plaintext: str) -> str:
        """加密字符串，返回 base64 编码的密文。"""
        if not isinstance(plaintext, str):
            raise EncryptionError("只能加密字符串类型数据")
        try:
            nonce = os.urandom(12)
            token = AESGCM(self._aes_key()).encrypt(
                nonce,
                plaintext.encode("utf-8"),
                self.APP_NAME.encode("utf-8"),
            )
            return "v2:" + base64.urlsafe_b64encode(nonce + token).decode("ascii")
        except Exception as exc:  # noqa: BLE001 - 统一包装密码学错误
            logger.error(f"加密失败: {exc}")
            raise EncryptionError(f"加密失败: {exc}")

    def decrypt(self, ciphertext: str) -> str:
        """解密 base64 编码的密文。"""
        if not isinstance(ciphertext, str):
            raise EncryptionError("只能解密字符串类型数据")
        try:
            if ciphertext.startswith("v2:"):
                payload = base64.urlsafe_b64decode(ciphertext[3:])
                if len(payload) < 29:
                    raise ValueError("密文长度无效")
                return AESGCM(self._aes_key()).decrypt(
                    payload[:12],
                    payload[12:],
                    self.APP_NAME.encode("utf-8"),
                ).decode("utf-8")

            # 兼容 0.1.0 版本使用 Fernet 保存的已有密文。
            f = Fernet(self.key)
            token = base64.urlsafe_b64decode(ciphertext)
            return f.decrypt(token).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - 统一包装密码学错误
            logger.error(f"解密失败: {exc}")
            raise EncryptionError(f"解密失败: {exc}")

    def _aes_key(self) -> bytes:
        """把旧版 Fernet 主密钥规范化为 32 字节 AES 密钥。"""
        key = self.key
        if len(key) == 32:
            return key
        try:
            decoded = base64.urlsafe_b64decode(key)
            if len(decoded) == 32:
                return decoded
        except (ValueError, TypeError):
            pass
        digest = hashes.Hash(hashes.SHA256())
        digest.update(key)
        return digest.finalize()
