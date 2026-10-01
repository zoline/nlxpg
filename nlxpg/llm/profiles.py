"""LLM 프로필 (DB) — 관리 → 모델 설정.

LLM 설정은 **DB에서만** 읽는다(2026-10-01 결정). `.env`에는 DB에 닿기 위한 값(시스템 DB 접속,
NLXPG_SECRET_KEY, 서버 포트)만 둔다. `.env`·pgxnl의 LLM 설정은 읽지 않는다.

역할(role): extract(문서 추출), testdoc(테스트 문서 작성). testdoc을 지정하지 않으면 extract를 쓴다.
"""
from __future__ import annotations

from typing import Any, Literal

import asyncpg

from nlxpg.crypto import decrypt, encrypt
from nlxpg.settings import Settings

Role = Literal["extract", "testdoc"]
#: 외부 API 제공자. 사내 문서를 보내면 안 된다(ADR-0008) — 문서 추출 역할(기본값)에 지정할 수 없고,
#: 새 설계에서 "사내 문서"가 아닌 실행에만 고를 수 있다.
EXTERNAL_PROVIDERS = frozenset({"anthropic"})
ROLES: dict[str, str] = {"extract": "문서 추출", "testdoc": "테스트 문서 작성"}

_PUBLIC_COLS = (
    "profile_id, name, provider, model, base_url, temperature, max_tokens, verify_ssl, guided_mode, "
    "watsonx_project_id, watsonx_space_id, watsonx_username, watsonx_instance_id, watsonx_api_version, "
    "created_at, updated_at, api_key_enc IS NOT NULL AS has_api_key, "
    "watsonx_password_enc IS NOT NULL AS has_password"
)
_EDITABLE = (
    "name", "provider", "model", "base_url", "temperature", "max_tokens", "verify_ssl", "guided_mode",
    "watsonx_project_id", "watsonx_space_id", "watsonx_username", "watsonx_instance_id", "watsonx_api_version",
)


class LLMNotConfigured(RuntimeError):
    """역할에 지정된 프로필이 없다."""


class ProfileStore:
    def __init__(self, pool: asyncpg.Pool, secret_key: str) -> None:
        self._pool = pool
        self._secret = secret_key

    # ── 프로필 ──────────────────────────────────────────
    async def list(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(f"""
            SELECT {_PUBLIC_COLS},
                   array(SELECT role FROM nlxpg_llm_roles r WHERE r.profile_id = p.profile_id ORDER BY role) AS roles
            FROM nlxpg_llm_profiles p ORDER BY name""")
        return [dict(r) for r in rows]

    async def get(self, profile_id: int) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT {_PUBLIC_COLS} FROM nlxpg_llm_profiles WHERE profile_id = $1", profile_id)
        return dict(row) if row else None

    async def save(self, data: dict[str, Any], profile_id: int | None = None) -> int:
        """새로 만들거나 고친다. api_key·password가 None이면 기존 값을 유지하고, ""이면 지운다."""
        fields = {k: data[k] for k in _EDITABLE if k in data}
        secrets = {}
        for plain_key, col in (("api_key", "api_key_enc"), ("password", "watsonx_password_enc")):
            if data.get(plain_key) is not None:
                secrets[col] = encrypt(data[plain_key], self._secret) if data[plain_key] else None
        values = {**fields, **secrets}
        if profile_id is None:
            cols = ", ".join(values)
            marks = ", ".join(f"${i + 1}" for i in range(len(values)))
            return await self._pool.fetchval(
                f"INSERT INTO nlxpg_llm_profiles ({cols}) VALUES ({marks}) RETURNING profile_id", *values.values())
        if values:
            sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(values))
            await self._pool.execute(
                f"UPDATE nlxpg_llm_profiles SET {sets}, updated_at = now() WHERE profile_id = $1",
                profile_id, *values.values())
        return profile_id

    async def delete(self, profile_id: int) -> bool:
        return (await self._pool.execute(
            "DELETE FROM nlxpg_llm_profiles WHERE profile_id = $1", profile_id)).endswith("1")

    # ── 역할 ────────────────────────────────────────────
    async def roles(self) -> dict[str, dict[str, Any]]:
        rows = await self._pool.fetch("""
            SELECT r.role, r.profile_id, r.disable_thinking, p.name, p.provider, p.model
            FROM nlxpg_llm_roles r JOIN nlxpg_llm_profiles p USING (profile_id)""")
        return {r["role"]: dict(r) for r in rows}

    async def set_role(self, role: Role, profile_id: int | None, *, disable_thinking: bool = False) -> None:
        if profile_id is not None and role == "extract":
            p = await self.get(profile_id)
            if p and p["provider"] in EXTERNAL_PROVIDERS:
                raise ValueError("외부 API 프로필은 문서 추출(기본) 역할에 지정할 수 없다 — "
                                 "새 설계에서 사내 문서가 아닐 때 골라 쓴다 (ADR-0008)")
        if profile_id is None:
            await self._pool.execute("DELETE FROM nlxpg_llm_roles WHERE role = $1", role)
            return
        await self._pool.execute("""
            INSERT INTO nlxpg_llm_roles (role, profile_id, disable_thinking) VALUES ($1, $2, $3)
            ON CONFLICT (role) DO UPDATE SET profile_id = EXCLUDED.profile_id,
                                             disable_thinking = EXCLUDED.disable_thinking""",
            role, profile_id, disable_thinking)

    # ── 실행용 설정 ─────────────────────────────────────
    async def profile_settings(self, profile_id: int, base: Settings, *, disable_thinking: bool = False) -> Settings:
        row = await self._pool.fetchrow("SELECT * FROM nlxpg_llm_profiles WHERE profile_id = $1", profile_id)
        if row is None:
            raise LLMNotConfigured(f"프로필 {profile_id}이(가) 없다")
        return to_settings(dict(row), base, self._secret, disable_thinking=disable_thinking)

    async def settings_for(self, role: Role, base: Settings) -> Settings:
        """역할에 지정된 프로필로 만든 Settings. testdoc이 없으면 extract를 쓴다."""
        roles = await self.roles()
        chosen = roles.get(role) or (roles.get("extract") if role == "testdoc" else None)
        if chosen is None:
            raise LLMNotConfigured(
                f"LLM이 설정되지 않았다({ROLES[role]}) — 웹 UI 관리 → 모델 설정에서 프로필을 등록하고 지정한다 "
                "(서버에서는 nlxpg setup llm)")
        return await self.profile_settings(chosen["profile_id"], base, disable_thinking=chosen["disable_thinking"])


def to_settings(row: dict[str, Any], base: Settings, secret_key: str, *, disable_thinking: bool = False) -> Settings:
    """프로필 행 → create_llm이 읽는 Settings 필드. 키·비밀번호는 여기서만 복호화한다."""
    api_key = decrypt(row["api_key_enc"], secret_key) if row.get("api_key_enc") else ""
    password = decrypt(row["watsonx_password_enc"], secret_key) if row.get("watsonx_password_enc") else ""
    common = {
        "llm_provider": row["provider"], "llm_model": row["model"], "llm_base_url": row["base_url"],
        "llm_temperature": row["temperature"], "llm_max_tokens": row["max_tokens"],
        "llm_guided_mode": row.get("guided_mode"), "llm_disable_thinking": disable_thinking,
        # 테스트 문서 작성용 .env 설정이 끼어들지 않게 비운다 (LLM은 DB에서만)
        "testdoc_llm_provider": None,
    }
    if row["provider"] == "watsonx":
        return base.model_copy(update={
            **common, "watsonx_project_id": row.get("watsonx_project_id"),
            "watsonx_space_id": row.get("watsonx_space_id"), "watsonx_username": row.get("watsonx_username"),
            "watsonx_instance_id": row.get("watsonx_instance_id"),
            "watsonx_api_version": row.get("watsonx_api_version") or "2024-05-01",
            "watsonx_verify_ssl": row["verify_ssl"], "watsonx_api_key": api_key, "watsonx_password": password,
        })
    return base.model_copy(update={**common, "llm_verify_ssl": row["verify_ssl"], "openai_api_key": api_key})
