"""환경변수 기반 설정.

LLM·임베딩·PostgreSQL 접속정보는 pgxnl과 공유한다(ADR-0004). 비밀번호를 두 곳에
복사해 두면 한쪽만 바뀌는 사고가 나므로, pgxnl/.env를 폴백으로 읽는다.

우선순위 (높은 것부터):
1. 실제 프로세스 환경변수
2. nlxpg/.env
3. pgxnl/.env (기본 ../pgxnl/.env, NLXPG_PGXNL_ENV로 변경)

각 필드는 NLXPG_* 이름을 먼저 보고, 없으면 대응하는 PGXNL_* 이름을 본다.
WATSONX_PASSWORD처럼 접두사 없는 비밀값도 같은 순서로 로드된다.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NLXPG_ENV = PROJECT_ROOT / ".env"

# override=False: 먼저 로드한 쪽이 이긴다. 그래서 nlxpg → pgxnl 순서로 부른다.
# pgxnl .env 위치(NLXPG_PGXNL_ENV)는 nlxpg .env를 읽은 **뒤에** 정한다 — 그래야 .env에 적은 값이
# 먹는다(전에는 먼저 정해서 .env의 NLXPG_PGXNL_ENV가 무시됐다).
load_dotenv(_NLXPG_ENV, override=False)
PGXNL_ENV = Path(os.environ.get("NLXPG_PGXNL_ENV", PROJECT_ROOT.parent / "pgxnl" / ".env"))
load_dotenv(PGXNL_ENV, override=False)


def _env(name: str, pgxnl_name: str | None = None) -> AliasChoices:
    """NLXPG_{name} → PGXNL_{pgxnl_name 또는 name} 순으로 찾는다."""
    return AliasChoices(f"NLXPG_{name}", f"PGXNL_{pgxnl_name or name}")


class Settings(BaseSettings):
    # 파일은 위에서 os.environ으로 이미 로드했으므로 env_file을 다시 지정하지 않는다.
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # LLM
    llm_provider: str = Field("openai", validation_alias=_env("LLM_PROVIDER"))
    llm_model: str = Field("Qwen/Qwen3-32B", validation_alias=_env("LLM_MODEL"))
    llm_base_url: str | None = Field(None, validation_alias=_env("LLM_BASE_URL"))
    llm_temperature: float = Field(0.0, validation_alias=_env("LLM_TEMPERATURE"))
    llm_max_tokens: int = Field(8192, validation_alias=_env("LLM_MAX_TOKENS"))
    #: 구조화 출력 강제 방식. None이면 provider에 맞춰 고른다(llm/factory.py).
    llm_guided_mode: str | None = Field(None, validation_alias="NLXPG_LLM_GUIDED_MODE")
    llm_disable_thinking: bool = Field(False, validation_alias="NLXPG_LLM_DISABLE_THINKING")
    llm_timeout: float = Field(300.0, validation_alias="NLXPG_LLM_TIMEOUT")
    #: OpenAI 호환(vLLM) 서버 인증서 검증. 사내 vLLM은 자체서명 인증서인 경우가 많다.
    llm_verify_ssl: bool = Field(True, validation_alias="NLXPG_LLM_VERIFY_SSL")

    # 테스트 문서 작성용 LLM (역합성). 비우면 위 LLM을 쓴다. 추출과 다른 모델로 문서를 써야
    # 같은 모델이 쓰고 읽어서 점수가 후하게 나오는 문제를 줄일 수 있다.
    testdoc_llm_provider: str | None = Field(None, validation_alias="NLXPG_TESTDOC_LLM_PROVIDER")
    testdoc_llm_model: str | None = Field(None, validation_alias="NLXPG_TESTDOC_LLM_MODEL")
    testdoc_llm_base_url: str | None = Field(None, validation_alias="NLXPG_TESTDOC_LLM_BASE_URL")
    testdoc_llm_verify_ssl: bool = Field(True, validation_alias="NLXPG_TESTDOC_LLM_VERIFY_SSL")
    testdoc_llm_max_tokens: int | None = Field(None, validation_alias="NLXPG_TESTDOC_LLM_MAX_TOKENS")
    #: 문서 작성에는 추론이 필요 없다. Qwen3 reasoning은 느리고 max_tokens를 소진한다(pgxnl 실측).
    testdoc_llm_disable_thinking: bool = Field(True, validation_alias="NLXPG_TESTDOC_LLM_DISABLE_THINKING")

    # watsonx 전용 (llm_provider="watsonx")
    watsonx_project_id: str | None = Field(None, validation_alias=_env("WATSONX_PROJECT_ID"))
    watsonx_space_id: str | None = Field(None, validation_alias=_env("WATSONX_SPACE_ID"))
    watsonx_username: str | None = Field(None, validation_alias=_env("WATSONX_USERNAME"))
    watsonx_api_version: str = Field("2024-05-01", validation_alias=_env("WATSONX_API_VERSION"))
    watsonx_verify_ssl: bool = Field(True, validation_alias=_env("WATSONX_VERIFY_SSL"))
    watsonx_instance_id: str | None = Field(None, validation_alias=_env("WATSONX_INSTANCE_ID"))

    # 임베딩 (엔티티 정합, 평가 의미 매칭)
    embedding_provider: str = Field("openai_compatible", validation_alias=_env("EMBEDDING_PROVIDER"))
    embedding_base_url: str | None = Field(None, validation_alias=_env("EMBEDDING_BASE_URL"))
    embedding_model: str | None = Field(None, validation_alias=_env("EMBEDDING_MODEL"))

    # PostgreSQL
    #: nlxpg 전용 시스템 DB(실행 이력, 산출 스키마, 평가·검토 결과). pgxnl 시스템 DB와
    #: 테이블이 섞이지 않도록 DB를 분리한다. 비워 두면 PGXNL_SYSTEM_PG_DSN에서 서버·계정은
    #: 그대로 두고 DB 이름만 system_db_name으로 바꿔 쓴다.
    system_pg_dsn: str | None = Field(None, validation_alias="NLXPG_SYSTEM_PG_DSN")
    system_db_name: str = Field("nlxpg", validation_alias="NLXPG_SYSTEM_DB_NAME")
    pgxnl_system_pg_dsn: str | None = Field(
        None, validation_alias="PGXNL_SYSTEM_PG_DSN", repr=False, exclude=True
    )
    #: DDL 샌드박스 검증용. 비워 두면 시스템 DB를 쓴다 — 검증은 임시 스키마 안에서
    #: 트랜잭션을 항상 롤백하므로 흔적을 남기지 않는다.
    sandbox_pg_dsn: str | None = Field(None, validation_alias="NLXPG_SANDBOX_PG_DSN")

    # 공통표준 (ADR-0005)
    apply_standards: bool = Field(True, validation_alias="NLXPG_APPLY_STANDARDS")
    standards_dir: Path = Field(PROJECT_ROOT / "data" / "standards", validation_alias="NLXPG_STANDARDS_DIR")
    #: 비우면 가장 최근 판 (예: 20251101)
    standards_version: str | None = Field(None, validation_alias="NLXPG_STANDARDS_VERSION")

    # 웹 UI (nlxpg serve). pgxnl은 8100을 쓴다.
    api_host: str = Field("0.0.0.0", validation_alias="NLXPG_API_HOST")
    api_port: int = Field(8200, validation_alias="NLXPG_API_PORT")
    #: 업로드 문서 저장 위치. 사내 문서가 올라올 수 있으므로 커밋되지 않는 data/private/ 아래에 둔다.
    upload_dir: Path = Field(PROJECT_ROOT / "data" / "private" / "uploads", validation_alias="NLXPG_UPLOAD_DIR")

    #: LLM 프로필의 키·비밀번호 암호화 키(nlxpg/crypto.py). 없으면 처음 쓸 때 만들어 .env에 쓴다.
    #: 잃으면 저장된 LLM 키를 다시 입력해야 한다.
    secret_key: str = Field("", validation_alias="NLXPG_SECRET_KEY", repr=False, exclude=True)

    #: 사용자가 한 명도 없을 때 서버가 만드는 첫 관리자(admin)의 초기 비밀번호. 비우면 무작위로
    #: 만들어 서버 로그에 한 번 남긴다. 어느 쪽이든 첫 로그인 때 바꾸게 한다.
    admin_initial_password: str = Field(
        "", validation_alias="NLXPG_ADMIN_INITIAL_PASSWORD", repr=False, exclude=True
    )

    # 파이프라인
    chunk_max_chars: int = Field(12_000, validation_alias="NLXPG_CHUNK_MAX_CHARS")
    extract_concurrency: int = Field(4, validation_alias="NLXPG_EXTRACT_CONCURRENCY")

    # 접두사 없는 비밀값. repr/로그에 찍히지 않도록 exclude + repr=False.
    openai_api_key: str = Field(
        "", validation_alias=AliasChoices("NLXPG_LLM_API_KEY", "OPENAI_API_KEY"), repr=False, exclude=True
    )
    testdoc_llm_api_key: str = Field(
        "", validation_alias="NLXPG_TESTDOC_LLM_API_KEY", repr=False, exclude=True
    )
    watsonx_api_key: str = Field("", validation_alias="WATSONX_API_KEY", repr=False, exclude=True)
    watsonx_password: str = Field("", validation_alias="WATSONX_PASSWORD", repr=False, exclude=True)
    embedding_api_key: str = Field("", validation_alias="EMBEDDING_API_KEY", repr=False, exclude=True)

    @model_validator(mode="after")
    def _derive_dsns(self) -> Settings:
        if not self.system_pg_dsn and self.pgxnl_system_pg_dsn:
            self.system_pg_dsn = replace_dbname(self.pgxnl_system_pg_dsn, self.system_db_name)
        if not self.sandbox_pg_dsn:
            self.sandbox_pg_dsn = self.system_pg_dsn
        return self

    @property
    def embedding_enabled(self) -> bool:
        if self.embedding_provider == "watsonx":
            return bool(self.embedding_model and (self.watsonx_project_id or self.watsonx_space_id))
        return bool(self.embedding_base_url and self.embedding_model)


def update_env_file(updates: dict[str, str], path: Path | None = None) -> Path:
    """nlxpg .env의 KEY=VALUE를 바꾸거나 덧붙인다. 주석과 순서는 그대로 둔다.
    `# KEY=` 처럼 주석 처리된 예시 줄이 있으면 그 자리에 채운다. 파일이 없으면 만든다(권한 600)."""
    path = path or _NLXPG_ENV
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") and "=" in stripped:
            key = stripped.lstrip("#").strip().split("=", 1)[0].strip()
            if key in pending:
                lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    new = not path.exists()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if new:
        path.chmod(0o600)
    return path


def ensure_secret_key(s: Settings) -> str:
    """NLXPG_SECRET_KEY가 없으면 만들어 .env에 쓰고 돌려준다."""
    if not s.secret_key:
        from nlxpg.crypto import new_secret_key

        s.secret_key = new_secret_key()
        update_env_file({"NLXPG_SECRET_KEY": s.secret_key})
        os.environ["NLXPG_SECRET_KEY"] = s.secret_key
    return s.secret_key


def replace_dbname(dsn: str, dbname: str) -> str:
    """postgresql://user:pw@host:port/db?opts 에서 db 부분만 바꾼다(쿼리 옵션은 유지)."""
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def mask_dsn(dsn: str | None) -> str:
    """로그·출력용. 비밀번호를 ***로 가린다."""
    if not dsn:
        return "(미설정)"
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


def load_settings() -> Settings:
    return Settings()
