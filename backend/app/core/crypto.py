"""API Key 加密存储（Fernet 对称加密）。

密钥优先级：环境变量 SECRET_KEY（.env）→ data/.secret_key（首次自动生成）。
存储格式：enc:<ciphertext>；未加密的旧值原样返回（向后兼容）。
"""
from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet

from backend.app.core.config import settings

_PREFIX = "enc:"

logger = logging.getLogger(__name__)

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
    except Exception as exc:  # noqa: BLE001
        # 密钥无法落盘时绝不能静默继续：下次启动会生成一把新密钥，
        # 库中已加密的 API Key 将永久无法解密，而界面仍显示"已配置"。
        raise RuntimeError(
            f"无法写入加密密钥文件 {key_file}：{exc}。"
            f"请检查目录写权限，或在 .env 中设置 SECRET_KEY 后重启。"
        ) from exc
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
    except Exception as exc:  # noqa: BLE001
        # 解密失败时绝不回吐密文：否则 "enc:xxx" 会被当作 API Key 原样送出。
        # 返回空串等价于"未配置"，调用方会提示用户重新填写。
        logger.warning("API Key 解密失败（密钥可能已变更），该配置值已忽略：%s", exc)
        return ""
