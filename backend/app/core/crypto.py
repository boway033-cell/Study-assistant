"""API Key 加密存储（Fernet 对称加密）。

密钥优先级：环境变量 SECRET_KEY（.env）→ data/.secret_key（首次自动生成）。
存储格式：enc:<ciphertext>；未加密的旧值原样返回（向后兼容）。
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet

from backend.app.core.config import settings

_PREFIX = "enc:"

_fernet: Fernet | None = None


def _load_key() -> bytes:
    raw = os.environ.get("SECRET_KEY", "")
    if raw:
        try:
            # 把任意长度 SECRET_KEY 派生为 32 字节 urlsafe base64 key
            return base64.urlsafe_b64encode(raw.encode()[:32].ljust(32, b"\x00"))
        except Exception:  # noqa: BLE001
            pass
    key_file = settings.data_dir / ".secret_key"
    if key_file.exists():
        try:
            return key_file.read_bytes()
        except Exception:  # noqa: BLE001
            pass
    key = Fernet.generate_key()
    try:
        key_file.write_bytes(key)
    except Exception:  # noqa: BLE001
        pass
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _PREFIX + _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value  # 旧数据（明文）原样返回，向后兼容
    try:
        return _get_fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception:  # noqa: BLE001
        return value
