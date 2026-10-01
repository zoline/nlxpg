"""사용자와 로그인 세션.

- 비밀번호: 표준 라이브러리 scrypt(N=2^14, r=8, p=1)로 해시한다. 새 의존성을 들이지 않는다.
- 세션: 무작위 토큰을 HttpOnly 쿠키로 주고 DB에는 SHA-256 해시만 둔다(DB가 새도 토큰을 못 쓴다).
- 역할: admin(사용자 관리·시스템 정보·모든 실행) / user(자기 실행만).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

SESSION_COOKIE = "nlxpg_session"
SESSION_TTL = timedelta(hours=12)
_N, _R, _P = 2**14, 8, 1
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def check_password_policy(password: str) -> str | None:
    """문제가 있으면 이유를, 없으면 None."""
    if len(password) < MIN_PASSWORD_LEN:
        return f"비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 한다"
    if password.isdigit() or password.isalpha():
        return "비밀번호에 숫자와 문자를 섞는다"
    return None


def temp_password() -> str:
    """관리자가 만들거나 초기화할 때 주는 임시 비밀번호(12자). 첫 로그인 때 바꾸게 한다.
    무작위 문자열이 우연히 영문자로만 되면(약 8%) 자체 정책에 걸리므로 정책을 만족할 때까지 다시 뽑는다."""
    while True:
        password = secrets.token_urlsafe(9)
        if check_password_policy(password) is None:
            return password


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    user_id: int
    username: str
    display_name: str
    email: str | None
    role: str
    enabled: bool
    must_change_password: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()} | {"is_admin": self.is_admin}


_USER_COLS = "user_id, username, display_name, email, role, enabled, must_change_password"


class UserStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @staticmethod
    def _user(row: asyncpg.Record | None) -> User | None:
        return User(**{k: row[k] for k in _USER_COLS.split(", ")}) if row else None

    async def count(self) -> int:
        return await self._pool.fetchval("SELECT count(*) FROM nlxpg_users")

    async def get(self, user_id: int) -> User | None:
        return self._user(await self._pool.fetchrow(
            f"SELECT {_USER_COLS} FROM nlxpg_users WHERE user_id = $1", user_id))

    async def list(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(f"""
            SELECT {_USER_COLS}, created_at, last_login_at,
                   (SELECT count(*) FROM nlxpg_runs r WHERE r.created_by = u.user_id) AS run_count
            FROM nlxpg_users u ORDER BY username""")
        return [dict(r) for r in rows]

    async def create(self, username: str, display_name: str, password: str, *, role: str = "user",
                     email: str | None = None, must_change: bool = True) -> User:
        row = await self._pool.fetchrow(
            f"""INSERT INTO nlxpg_users (username, display_name, email, role, password_hash, must_change_password)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING {_USER_COLS}""",
            username, display_name, email, role, hash_password(password), must_change)
        return self._user(row)  # type: ignore[return-value]

    async def update(self, user_id: int, **fields: Any) -> User | None:
        allowed = {"display_name", "email", "role", "enabled"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if sets:
            cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
            await self._pool.execute(f"UPDATE nlxpg_users SET {cols} WHERE user_id = $1",
                                     user_id, *sets.values())
            if sets.get("enabled") is False:  # 사용 중지하면 로그인 세션도 끊는다
                await self._pool.execute("DELETE FROM nlxpg_sessions WHERE user_id = $1", user_id)
        return await self.get(user_id)

    async def set_password(self, user_id: int, password: str, *, must_change: bool) -> None:
        await self._pool.execute(
            "UPDATE nlxpg_users SET password_hash = $2, must_change_password = $3 WHERE user_id = $1",
            user_id, hash_password(password), must_change)

    async def admin_count(self, *, enabled_only: bool = True) -> int:
        return await self._pool.fetchval(
            "SELECT count(*) FROM nlxpg_users WHERE role = 'admin'" + (" AND enabled" if enabled_only else ""))

    # ── 로그인 ──────────────────────────────────────────
    async def authenticate(self, username: str, password: str) -> User | None:
        row = await self._pool.fetchrow(
            f"SELECT {_USER_COLS}, password_hash FROM nlxpg_users WHERE username = $1", username)
        # 사용자가 없어도 해시 계산을 한 번 해서 응답 시간으로 계정 존재를 알 수 없게 한다
        if row is None:
            verify_password(password, hash_password("dummy-password"))
            return None
        if not row["enabled"] or not verify_password(password, row["password_hash"]):
            return None
        await self._pool.execute("UPDATE nlxpg_users SET last_login_at = now() WHERE user_id = $1",
                                 row["user_id"])
        return self._user(row)

    async def check_password(self, user_id: int, password: str) -> bool:
        stored = await self._pool.fetchval("SELECT password_hash FROM nlxpg_users WHERE user_id = $1", user_id)
        return bool(stored) and verify_password(password, stored)

    async def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        await self._pool.execute("DELETE FROM nlxpg_sessions WHERE expires_at < now()")
        await self._pool.execute(
            "INSERT INTO nlxpg_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
            _token_hash(token), user_id, datetime.now(UTC) + SESSION_TTL)
        return token

    async def session_user(self, token: str) -> User | None:
        row = await self._pool.fetchrow(
            f"""SELECT {', '.join('u.' + c for c in _USER_COLS.split(', '))}
                FROM nlxpg_sessions s JOIN nlxpg_users u USING (user_id)
                WHERE s.token_hash = $1 AND s.expires_at > now() AND u.enabled""",
            _token_hash(token))
        return self._user(row)

    async def delete_session(self, token: str) -> None:
        await self._pool.execute("DELETE FROM nlxpg_sessions WHERE token_hash = $1", _token_hash(token))

    async def delete_user_sessions(self, user_id: int, *, keep: str | None = None) -> None:
        await self._pool.execute(
            "DELETE FROM nlxpg_sessions WHERE user_id = $1 AND token_hash <> $2",
            user_id, _token_hash(keep) if keep else "")
