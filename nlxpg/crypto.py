"""LLM 프로필의 키·비밀번호 암호화 (pgxnl auth/security.py와 같은 방식: Fernet).

키는 NLXPG_SECRET_KEY(.env)에서 SHA-256으로 유도한다. 이 값을 잃으면 저장된 키를 복호화할 수 없다
— 프로필의 키만 다시 입력하면 된다. 서버가 처음 뜰 때 없으면 만들어 .env에 쓴다(api/app.py).
"""
from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken


def new_secret_key() -> str:
    return secrets.token_urlsafe(32)


def _fernet(secret_key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


def encrypt(plain: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(plain.encode()).decode()


def decrypt(token: str, secret_key: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("저장된 키를 복호화할 수 없다 — NLXPG_SECRET_KEY가 바뀌었다. 프로필의 키를 다시 입력한다") from exc
