import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import cfg

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(cfg.app_secret_key.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) < 10:
        return "****"
    return f"{key[:3]}...{key[-4:]}"
