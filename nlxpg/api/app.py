"""nlxpg 웹 UI 백엔드.

pgxnl과 같은 구성이다: FastAPI가 단일 index.html과 JSON API를 함께 서빙한다.

로그인이 필요하다(nlxpg/auth.py). 역할은 admin / user 두 단계이고, 일반 사용자는 자기가
만든 실행만 보고·지우고·검토한다. 남의 실행은 404로 답해 존재 여부도 알리지 않는다.

설계 실행은 오래 걸리므로(섹션마다 LLM 호출) POST /api/runs가 run_id를 먼저 돌려주고
백그라운드에서 돌린다. 진행 상황은 프로세스 메모리에만 있다 — 서버를 재시작하면
진행 중이던 실행은 DB에 'running'으로 남는다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nlxpg import __version__
from nlxpg.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    User,
    UserStore,
    check_password_policy,
    temp_password,
)
from nlxpg.generate import to_mermaid
from nlxpg.ir import SchemaIR
from nlxpg.llm.profiles import EXTERNAL_PROVIDERS, ROLES, LLMNotConfigured, ProfileStore
from nlxpg.parsing import Document, load_document
from nlxpg.service import RunService, delete_run
from nlxpg.settings import PROJECT_ROOT, Settings, ensure_secret_key, load_settings, mask_dsn
from nlxpg.standards import Standards, load_standards, resolve_attribute, resolve_entity
from nlxpg.standards.check import VERDICT_LABEL, check_columns
from nlxpg.standards.local import (
    KIND_LABEL,
    LocalConflict,
    LocalItem,
    LocalStore,
    apply_local,
    check_item,
    dependents,
    from_csv,
    import_items,
    to_csv,
)
from nlxpg.standards.resolve import normalize_name
from nlxpg.store import REVIEW_ITEMS, RunStore
from nlxpg.validate.normalform import check_normal_forms

log = logging.getLogger(__name__)
#: 첫 관리자 초기 비밀번호를 남기는 파일 (커밋되지 않는 data/private/). 비밀번호를 바꾸면 지운다.
INITIAL_PASSWORD_FILE = Path(__file__).resolve().parents[2] / "data" / "private" / "initial_admin_password.txt"
_STATIC = Path(__file__).parent / "static"
_SAFE_NAME = re.compile(r"[^0-9A-Za-z가-힣._-]+")


class State:
    def __init__(self) -> None:
        self.settings: Settings = load_settings()
        #: 공통표준(CSV) 위에 기관 추가 표준(DB)을 덧붙인 것. 설계·검증은 이것을 쓴다(ADR-0009)
        self.standards: Standards | None = None
        #: 공통표준 원본(CSV만)
        self.base_standards: Standards | None = None
        self.local: LocalStore | None = None
        self.local_applied: list[LocalItem] = []
        self.local_rejected: list[tuple[LocalItem, str]] = []
        self.store: RunStore | None = None
        self.users: UserStore | None = None
        self.profiles: ProfileStore | None = None
        self.db_error: str | None = None
        #: run_id → 진행 상황 (메모리)
        self.progress: dict[int, dict[str, Any]] = {}
        self.tasks: set[asyncio.Task] = set()


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.settings = load_settings()
    s = state.settings
    try:
        state.base_standards = state.standards = load_standards(s.standards_dir, s.standards_version)
    except FileNotFoundError as exc:
        log.warning("공통표준 없음: %s", exc)
    if s.system_pg_dsn:
        try:
            state.store = await RunStore.connect(s.system_pg_dsn)
            state.users = UserStore(state.store.pool)
            state.profiles = ProfileStore(state.store.pool, ensure_secret_key(s))
            state.local = LocalStore(state.store.pool)
            await _bootstrap_admin(state.users, s)
            await _reload_local()
        except Exception as exc:  # noqa: BLE001 — 접속 실패는 화면에 503으로 알린다
            state.db_error = f"{type(exc).__name__}: {exc}"
            log.warning("시스템 DB 접속 실패: %s", state.db_error)
    yield
    for t in list(state.tasks):
        t.cancel()
    if state.store:
        await state.store.close()


async def _reload_local() -> None:
    """공통표준 원본 위에 DB의 기관 추가 표준을 다시 덧붙인다(ADR-0009). 바꾼 즉시 부른다."""
    if state.base_standards is None or state.local is None:
        return
    applied = apply_local(state.base_standards, await state.local.list())
    state.standards, state.local_applied, state.local_rejected = (
        applied.standards, applied.applied, applied.rejected)
    for item, why in applied.rejected:
        log.warning("기관 표준 적용 안 됨: %s '%s' — %s", item.kind, item.name, why)


async def _bootstrap_admin(users: UserStore, s: Settings) -> None:
    """사용자가 한 명도 없으면 첫 관리자 admin을 만든다(pgxnl의 ADMIN_INITIAL_PASSWORD와 같은 방식).
    NLXPG_ADMIN_INITIAL_PASSWORD가 없으면 무작위로 만들어 로그에 한 번만 남긴다. 첫 로그인 때 바꾸게 한다."""
    if await users.count():
        return
    password = s.admin_initial_password or temp_password()
    await users.create("admin", "관리자", password, role="admin", must_change=True)
    if s.admin_initial_password:
        log.warning("첫 관리자 'admin'을 만들었다 (비밀번호: NLXPG_ADMIN_INITIAL_PASSWORD). 첫 로그인 때 바꿔야 한다.")
        return
    # 로그는 재기동 때 덮어써질 수 있어(2026-10-01 실제로 잃었다) 파일에도 남긴다. 비밀번호를 바꾸면 지운다.
    INITIAL_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    INITIAL_PASSWORD_FILE.write_text(
        f"nlxpg 첫 관리자\n아이디: admin\n임시 비밀번호: {password}\n"
        "첫 로그인 때 비밀번호를 바꾸면 이 파일은 지워진다. 잃어버리면: nlxpg users passwd admin\n",
        encoding="utf-8")
    INITIAL_PASSWORD_FILE.chmod(0o600)
    log.warning("첫 관리자 'admin'을 만들었다. 초기 비밀번호: %s (%s에도 남김, 첫 로그인 때 바꿔야 한다)",
                password, INITIAL_PASSWORD_FILE)


app = FastAPI(title="nlxpg", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def index() -> FileResponse:
    # 단일 파일 UI라 무엇을 고쳐도 이 응답이 바뀐다. 낡은 판이 캐시되지 않게 매번 재검증한다.
    return FileResponse(_STATIC / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})


def _store() -> RunStore:
    if state.store is None:
        raise HTTPException(503, f"시스템 DB에 연결되지 않았다: {state.db_error or '설정 없음'}")
    return state.store


def _users() -> UserStore:
    if state.users is None:
        raise HTTPException(503, f"시스템 DB에 연결되지 않았다: {state.db_error or '설정 없음'}")
    return state.users


async def session_user(request: Request) -> User:
    """로그인한 사용자. 비밀번호를 바꿔야 하는 상태여도 통과한다(비밀번호 변경·로그아웃용)."""
    token = request.cookies.get(SESSION_COOKIE)
    user = await _users().session_user(token) if token else None
    if user is None:
        raise HTTPException(401, "로그인이 필요하다")
    return user


async def current_user(user: Annotated[User, Depends(session_user)]) -> User:
    if user.must_change_password:
        raise HTTPException(403, "비밀번호를 먼저 바꿔야 한다")
    return user


async def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 할 수 있다")
    return user


AnyUser = Annotated[User, Depends(session_user)]
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]


async def _own_run(run_id: int, user: User) -> None:
    """관리자가 아니면 자기 실행만. 남의 실행은 없는 것처럼 404."""
    exists, owner = await _store().run_owner(run_id)
    if not exists or (not user.is_admin and owner != user.user_id):
        raise HTTPException(404, "실행 이력이 없다")


def _profiles() -> ProfileStore:
    if state.profiles is None:
        raise HTTPException(503, f"시스템 DB에 연결되지 않았다: {state.db_error or '설정 없음'}")
    return state.profiles


async def _llm_settings(role: str = "extract") -> Settings:
    """역할의 LLM 설정(DB 프로필). 없으면 400 — 관리 → 모델 설정에서 등록해야 한다."""
    try:
        return await _profiles().settings_for(role, state.settings)  # type: ignore[arg-type]
    except LLMNotConfigured as exc:
        raise HTTPException(400, str(exc)) from exc


def _standards() -> Standards:
    if state.standards is None:
        raise HTTPException(503, "공통표준 CSV가 없다 (data/standards/)")
    return state.standards


# ── 로그인 ──────────────────────────────────────────────

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response) -> dict[str, Any]:
    users = _users()
    user = await users.authenticate(body.username.strip(), body.password)
    if user is None:
        await asyncio.sleep(0.5)  # 무차별 대입을 늦춘다
        raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않는다")
    token = await users.create_session(user.user_id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=int(SESSION_TTL.total_seconds()))
    return user.public()


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if token and state.users:
        await state.users.delete_session(token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: AnyUser) -> dict[str, Any]:
    return user.public()


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=50)
    email: str | None = Field(None, max_length=200)


@app.patch("/api/auth/me")
async def update_me(body: ProfileIn, user: CurrentUser) -> dict[str, Any]:
    updated = await _users().update(user.user_id, **body.model_dump())
    return updated.public()  # type: ignore[union-attr]


class PasswordIn(BaseModel):
    current: str
    new: str


@app.post("/api/auth/password")
async def change_password(body: PasswordIn, user: AnyUser, request: Request) -> dict[str, Any]:
    users = _users()
    if not await users.check_password(user.user_id, body.current):
        raise HTTPException(400, "현재 비밀번호가 맞지 않는다")
    if body.new == body.current:
        raise HTTPException(400, "새 비밀번호가 지금 것과 같다")
    if problem := check_password_policy(body.new):
        raise HTTPException(400, problem)
    await users.set_password(user.user_id, body.new, must_change=False)
    await users.delete_user_sessions(user.user_id, keep=request.cookies.get(SESSION_COOKIE))
    if INITIAL_PASSWORD_FILE.exists() and f"아이디: {user.username}\n" in INITIAL_PASSWORD_FILE.read_text(encoding="utf-8"):
        INITIAL_PASSWORD_FILE.unlink()
    return {"ok": True}


# ── 사용자 관리 (관리자) ────────────────────────────────


@app.get("/api/users")
async def list_users(admin: AdminUser) -> list[dict[str, Any]]:
    return await _users().list()


class UserIn(BaseModel):
    username: str
    display_name: str = Field(min_length=1, max_length=50)
    email: str | None = None
    role: Literal["admin", "user"] = "user"
    #: 비우면 임시 비밀번호를 만들어 응답에 한 번만 담는다
    password: str | None = None


@app.post("/api/users")
async def create_user(body: UserIn, admin: AdminUser) -> dict[str, Any]:
    if not _USERNAME.match(body.username):
        raise HTTPException(400, "아이디는 영문·숫자·_·.·- 3~32자")
    if body.password and (problem := check_password_policy(body.password)):
        raise HTTPException(400, problem)
    password = body.password or temp_password()
    try:
        user = await _users().create(body.username, body.display_name, password, role=body.role,
                                     email=body.email or None, must_change=True)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(400, f"이미 있는 아이디다: {body.username}") from exc
    log.info("사용자 생성: %s (%s) by %s", user.username, user.role, admin.username)
    return {"user": user.public(), "temp_password": None if body.password else password}


class UserPatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=50)
    email: str | None = None
    role: Literal["admin", "user"] | None = None
    enabled: bool | None = None


@app.patch("/api/users/{user_id}")
async def update_user(user_id: int, body: UserPatch, admin: AdminUser) -> dict[str, Any]:
    users = _users()
    target = await users.get(user_id)
    if target is None:
        raise HTTPException(404, "사용자가 없다")
    losing_admin = target.is_admin and target.enabled and (body.role == "user" or body.enabled is False)
    if losing_admin and await users.admin_count() <= 1:
        raise HTTPException(400, "마지막 관리자는 역할을 바꾸거나 사용 중지할 수 없다")
    updated = await users.update(user_id, **body.model_dump())
    log.info("사용자 변경: %s %s by %s", target.username, body.model_dump(exclude_none=True), admin.username)
    return updated.public()  # type: ignore[union-attr]


class ResetIn(BaseModel):
    password: str | None = None


@app.post("/api/users/{user_id}/reset-password")
async def reset_password(user_id: int, body: ResetIn, admin: AdminUser) -> dict[str, Any]:
    users = _users()
    target = await users.get(user_id)
    if target is None:
        raise HTTPException(404, "사용자가 없다")
    if body.password and (problem := check_password_policy(body.password)):
        raise HTTPException(400, problem)
    password = body.password or temp_password()
    await users.set_password(user_id, password, must_change=True)
    await users.delete_user_sessions(user_id)
    log.info("비밀번호 초기화: %s by %s", target.username, admin.username)
    return {"temp_password": None if body.password else password}


# ── 정보 ────────────────────────────────────────────────


@app.get("/api/info")
async def info(user: CurrentUser) -> dict[str, Any]:
    """화면 상단·시스템 정보 화면용. 비밀값(키·비밀번호)은 담지 않는다."""
    s = state.settings
    std = state.standards
    roles = await state.profiles.roles() if state.profiles else {}

    def role_info(role: str) -> dict[str, Any] | None:
        r = roles.get(role)
        return {"profile": r["name"], "provider": r["provider"], "model": r["model"],
                "disable_thinking": r["disable_thinking"]} if r else None

    return {
        "version": __version__,
        "llm": role_info("extract"),
        "testdoc_llm": role_info("testdoc"),
        "llm_configured": "extract" in roles,
        "standards_version": std.version if std else None,
        "standards": {"words": len(std.words), "terms": len(std.terms), "domains": len(std.domains),
                      "local_items": std.local_items, "local_rejected": len(state.local_rejected),
                      "dir": str(s.standards_dir)} if std else None,
        "apply_standards": s.apply_standards,
        "system_db": mask_dsn(s.system_pg_dsn),
        "db_ok": state.store is not None,
        "db_error": state.db_error,
        "sandbox": bool(s.sandbox_pg_dsn),
        "sandbox_db": mask_dsn(s.sandbox_pg_dsn),
        "env_file": str(PROJECT_ROOT / ".env"),
        "review_items": list(REVIEW_ITEMS),
    }


# ── 모델 설정 (관리자) ──────────────────────────────────


class LLMProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["openai", "watsonx", "anthropic"]
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(8192, ge=64, le=262144)
    verify_ssl: bool = True
    guided_mode: Literal["json_schema", "json_object", "none"] | None = None
    #: None이면 기존 값 유지(수정 시), ""이면 지운다
    api_key: str | None = None
    password: str | None = None
    watsonx_project_id: str | None = None
    watsonx_space_id: str | None = None
    watsonx_username: str | None = None
    watsonx_instance_id: str | None = None
    watsonx_api_version: str = "2024-05-01"


@app.get("/api/llm/profiles")
async def llm_profiles(admin: AdminUser) -> dict[str, Any]:
    return {"profiles": await _profiles().list(), "roles": await _profiles().roles(), "role_names": ROLES}


@app.post("/api/llm/profiles")
async def create_profile(body: LLMProfileIn, admin: AdminUser) -> dict[str, Any]:
    try:
        pid = await _profiles().save(body.model_dump())
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(400, f"이미 있는 이름이다: {body.name}") from exc
    # 처음 만든 프로필이면 추출 역할에 바로 지정한다 (첫 설정을 한 번에 끝내게).
    # 외부 API 프로필은 기본 추출 역할에 넣지 않는다(ADR-0008).
    if body.provider not in EXTERNAL_PROVIDERS and "extract" not in await _profiles().roles():
        await _profiles().set_role("extract", pid)
    log.info("LLM 프로필 생성: %s by %s", body.name, admin.username)
    return {"profile_id": pid}


@app.put("/api/llm/profiles/{profile_id}")
async def update_profile(profile_id: int, body: LLMProfileIn, admin: AdminUser) -> dict[str, Any]:
    if await _profiles().get(profile_id) is None:
        raise HTTPException(404, "프로필이 없다")
    try:
        await _profiles().save(body.model_dump(), profile_id)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(400, f"이미 있는 이름이다: {body.name}") from exc
    return {"profile_id": profile_id}


@app.delete("/api/llm/profiles/{profile_id}")
async def delete_profile(profile_id: int, admin: AdminUser) -> dict[str, Any]:
    if not await _profiles().delete(profile_id):
        raise HTTPException(404, "프로필이 없다")
    return {"ok": True}


@app.post("/api/llm/profiles/{profile_id}/test")
async def test_profile(profile_id: int, admin: AdminUser) -> dict[str, Any]:
    """구조화 출력 한 번을 실제로 호출해 본다."""
    import time

    from pydantic import BaseModel as _BM

    from nlxpg.llm import create_llm

    class Ping(_BM):
        answer: str

    try:
        ls = await _profiles().profile_settings(profile_id, state.settings)
        llm = create_llm(ls)
    except (LLMNotConfigured, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    started = time.monotonic()
    try:
        r = await llm.complete_json("질문에 짧게 답한다.", '"ok"라고 answer 필드에 답하라.', Ping)
        return {"ok": True, "answer": r.answer, "seconds": round(time.monotonic() - started, 2),
                "guided_mode": llm.guided_mode}
    except Exception as exc:  # noqa: BLE001 — 접속 오류를 화면에 그대로 보여준다
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 2)}
    finally:
        await llm.aclose()


class RoleIn(BaseModel):
    profile_id: int | None
    disable_thinking: bool = False


@app.put("/api/llm/roles/{role}")
async def set_role(role: Literal["extract", "testdoc"], body: RoleIn, admin: AdminUser) -> dict[str, Any]:
    if body.profile_id is not None and await _profiles().get(body.profile_id) is None:
        raise HTTPException(404, "프로필이 없다")
    try:
        await _profiles().set_role(role, body.profile_id, disable_thinking=body.disable_thinking)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"roles": await _profiles().roles()}


@app.get("/api/llm/choices")
async def llm_choices(user: CurrentUser) -> dict[str, Any]:
    """새 설계 화면에서 고를 수 있는 프로필. 키·주소는 담지 않는다."""
    roles = await _profiles().roles()
    profiles = [
        {"profile_id": p["profile_id"], "name": p["name"], "provider": p["provider"], "model": p["model"],
         "external": p["provider"] in EXTERNAL_PROVIDERS}
        for p in await _profiles().list()
    ]
    return {"default": roles["extract"]["profile_id"] if "extract" in roles else None, "profiles": profiles}


async def _run_llm_settings(profile_id: int | None, internal: bool) -> tuple[Settings, dict[str, Any]]:
    """새 설계에 쓸 LLM. 고르지 않으면 문서 추출 역할. 외부 API 프로필은 사내 문서에 쓸 수 없다."""
    roles = await _profiles().roles()
    if profile_id is None or (roles.get("extract") or {}).get("profile_id") == profile_id:
        ls = await _llm_settings("extract")
        r = roles["extract"]
        return ls, {"llm_profile": r["name"], "external_api": r["provider"] in EXTERNAL_PROVIDERS}
    p = await _profiles().get(profile_id)
    if p is None:
        raise HTTPException(404, "프로필이 없다")
    external = p["provider"] in EXTERNAL_PROVIDERS
    if external and internal:
        raise HTTPException(400, f"'{p['name']}'은(는) 외부 API라 사내 문서에 쓸 수 없다 (ADR-0008). "
                                 "사내 문서가 아니면 '사내 문서' 체크를 끈다")
    # 이 프로필이 지정된 역할이 있으면 그 역할의 "생각 끄기"를 따른다
    think_off = any(r["disable_thinking"] for r in roles.values() if r["profile_id"] == profile_id)
    ls = await _profiles().profile_settings(profile_id, state.settings, disable_thinking=think_off)
    return ls, {"llm_profile": p["name"], "external_api": external}


# ── 설계 실행 ──────────────────────────────────────────


@app.get("/api/runs")
async def list_runs(user: CurrentUser, limit: int = 100) -> list[dict[str, Any]]:
    runs = await _store().list_runs(limit, owner=None if user.is_admin else user.user_id)
    for r in runs:
        r["progress"] = state.progress.get(r["run_id"])
    return runs


@app.post("/api/runs")
async def create_run(
    files: Annotated[list[UploadFile], File()],
    label: Annotated[str, Form()] = "",
    internal: Annotated[bool, Form()] = False,
    sandbox: Annotated[bool, Form()] = True,
    profile_id: Annotated[int | None, Form()] = None,
    debug: Annotated[bool, Form()] = False,
    *, user: CurrentUser,
) -> dict[str, Any]:
    store = _store()
    s = state.settings
    # LLM이 설정돼 있지 않거나 사내 문서에 외부 API를 고르면 파일을 받기 전에 막는다
    llm_settings, llm_config = await _run_llm_settings(profile_id, internal)
    folder = s.upload_dir / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    docs: list[Document] = []
    for f in files:
        name = _SAFE_NAME.sub("_", Path(f.filename or "document").name)
        path = folder / name
        path.write_bytes(await f.read())
        try:
            docs.append(load_document(path))
        except (ValueError, NotImplementedError, RuntimeError, UnicodeDecodeError) as exc:
            raise HTTPException(400, f"{f.filename}: {exc}") from exc

    service = RunService(s, state.standards if s.apply_standards else None, store, llm_settings)
    run_id = await service.start(
        docs, label=label or None, internal=internal,
        extra_config={"upload_dir": folder.name, "debug": debug, **llm_config},  # upload_dir: 실행을 지울 때 이 폴더도 정리한다
        created_by=user.user_id,
    )
    assert run_id is not None
    prog: dict[str, Any] = {"stage": "queued", "docs": {}, "error": None, "finished": False}
    state.progress[run_id] = prog

    def on_progress(event: dict[str, Any]) -> None:
        prog["stage"] = event["stage"]
        if event["stage"] == "extract":
            prog["docs"][event["doc_id"]] = {"done": event["done"], "total": event["total"]}
            if event.get("section"):
                prog["last_section"] = event["section"]

    async def run() -> None:
        try:
            await service.execute(run_id, docs, sandbox=sandbox, progress=on_progress, debug=debug)
            prog["stage"] = "done"
        except Exception as exc:  # 실패는 DB(finish_run)와 진행 상황에 남긴다
            log.exception("run %s 실패", run_id)
            prog["stage"] = "failed"
            prog["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            prog["finished"] = True

    task = asyncio.create_task(run())
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: int, user: CurrentUser) -> dict[str, Any]:
    await _own_run(run_id, user)
    run = await _store().get_run(run_id)
    if run is None:
        raise HTTPException(404, "실행 이력이 없다")
    run["progress"] = state.progress.get(run_id)
    if run.get("schema_ir"):
        ir = SchemaIR.model_validate(run["schema_ir"])
        run["erd"] = to_mermaid(ir)
        # 저장된 결과가 아니라 매번 계산한다 — 규칙을 고치면 이전 실행에도 바로 반영되고,
        # 검사 도입 전 실행(검증 결과에 normal_forms가 없음)도 볼 수 있다.
        run["normal_forms"] = [asdict(f) for f in check_normal_forms(ir, state.standards)]
    return run


@app.get("/api/runs/{run_id}/llm-calls")
async def run_llm_calls(run_id: int, user: CurrentUser) -> list[dict[str, Any]]:
    """디버그 모드로 실행한 경우의 LLM 호출 기록."""
    await _own_run(run_id, user)
    return await _store().list_llm_calls(run_id)


@app.delete("/api/runs/{run_id}")
async def remove_run(run_id: int, user: CurrentUser) -> dict[str, Any]:
    """실행 이력 삭제. 검토 점수도 함께 지워지고, 이 실행만 쓰던 원문·업로드 파일도 지운다."""
    await _own_run(run_id, user)
    prog = state.progress.get(run_id)
    if prog and not prog.get("finished"):
        raise HTTPException(409, "진행 중인 실행은 지울 수 없다. 끝난 뒤 다시 시도한다.")
    result = await delete_run(_store(), state.settings, run_id)
    if result is None:
        raise HTTPException(404, "실행 이력이 없다")
    state.progress.pop(run_id, None)
    log.info("run %s 삭제 by %s: %s", run_id, user.username, result)
    return {"run_id": run_id, **result}


@app.get("/api/runs/{run_id}/ddl", response_class=PlainTextResponse)
async def get_ddl(run_id: int, user: CurrentUser) -> PlainTextResponse:
    await _own_run(run_id, user)
    run = await _store().get_run(run_id)
    if not run or not run.get("ddl"):
        raise HTTPException(404, "DDL이 없다")
    return PlainTextResponse(
        run["ddl"], headers={"Content-Disposition": f'attachment; filename="nlxpg_run{run_id}.sql"'}
    )


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str, user: CurrentUser) -> dict[str, Any]:
    if not user.is_admin and not await _store().document_visible_to(doc_id, user.user_id):
        raise HTTPException(404, "문서가 없다")
    doc = await _store().get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "문서가 없다")
    return doc


# ── 사람 검토 ──────────────────────────────────────────


class ReviewIn(BaseModel):
    #: 비우면 로그인한 사용자 이름
    reviewer: str | None = None
    entity_completeness: int | None = Field(None, ge=1, le=5)
    normalization: int | None = Field(None, ge=1, le=5)
    keys_relationships: int | None = Field(None, ge=1, le=5)
    data_types: int | None = Field(None, ge=1, le=5)
    naming: int | None = Field(None, ge=1, le=5)
    evidence: int | None = Field(None, ge=1, le=5)
    comment: str | None = None


@app.get("/api/runs/{run_id}/reviews")
async def list_reviews(run_id: int, user: CurrentUser) -> list[dict[str, Any]]:
    await _own_run(run_id, user)
    return await _store().list_reviews(run_id)


@app.post("/api/runs/{run_id}/reviews")
async def add_review(run_id: int, review: ReviewIn, user: CurrentUser) -> dict[str, Any]:
    await _own_run(run_id, user)
    data = review.model_dump()
    data["reviewer"] = (data["reviewer"] or "").strip() or user.display_name
    review_id = await _store().add_review(run_id, data)
    return {"review_id": review_id}


# ── 공통표준 ────────────────────────────────────────────


@app.get("/api/standards/lookup")
async def standards_lookup(user: CurrentUser, names: str, entity: bool = False) -> list[dict[str, Any]]:
    std = _standards()
    out = []
    for name in [n.strip() for n in re.split(r"[\n,]", names) if n.strip()]:
        r = resolve_entity(std, name) if entity else resolve_attribute(std, name)
        out.append({"input": name, **r.__dict__})
    return out


@app.get("/api/standards/search")
async def standards_search(
    user: CurrentUser, q: str, kind: Literal["term", "word", "domain"] = "term", limit: int = 100
) -> dict[str, Any]:
    std = _standards()
    q_up = q.strip().upper()
    if not q_up:
        return {"total": 0, "items": []}
    if kind == "term":
        pool = [t.__dict__ for t in std.terms.values()]
        keys = ("name", "abbr", "description")
    elif kind == "word":
        pool = [w.__dict__ for w in std.words.values()]
        keys = ("name", "abbr", "english")
    else:
        pool = [d.__dict__ for d in std.domains.values()]
        keys = ("name", "klass", "group", "description")
    hits = [x for x in pool if any(q_up in str(x[k]).upper() for k in keys)]
    # 이름이 정확히 같거나 이름으로 시작하는 것을 앞에 둔다
    hits.sort(key=lambda x: (x["name"].upper() != q_up, not x["name"].upper().startswith(q_up), x["name"]))
    return {"total": len(hits), "items": hits[:limit]}


#: 매뉴얼 [표 Ⅳ-29]의 도메인 그룹 순서
_GROUP_ORDER = ["금액", "날짜/시간", "내용", "명칭", "번호", "수량", "율", "코드"]


@app.get("/api/standards/domains")
async def standards_domains(user: CurrentUser) -> dict[str, Any]:
    """공통표준도메인 전체. nlxpg가 실제로 쓰는 PostgreSQL 타입(ADR-0005)과 사용 용어 수를 함께 준다."""
    from nlxpg.standards.resolve import pg_type

    std = _standards()
    fmt: dict[str, list[str]] = {}
    for w in std.words.values():
        if w.is_format and w.domain_class:
            fmt.setdefault(w.domain_class, []).append(w.name)
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in std.domains.values():
        adapted, original = pg_type(d), pg_type(d, adapt_dates=False)
        groups.setdefault(d.group, []).append({
            **d.__dict__, "pg_type": adapted, "original_type": original, "adapted": adapted != original,
            "term_count": std.domain_term_count.get(d.name, 0),
            "default": std.default_domain.get(d.klass) == d.name,
        })
    order = {g: i for i, g in enumerate(_GROUP_ORDER)}
    return {
        "version": std.version,
        "total": len(std.domains),
        "groups": [
            {"group": g, "domains": sorted(ds, key=lambda x: (x["klass"], x["data_type"], x["length"] or 0, x["scale"] or 0))}
            for g, ds in sorted(groups.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
        ],
        "format_words": {k: sorted(v) for k, v in fmt.items()},
    }


@app.get("/api/standards/domains/{name}/terms")
async def standards_domain_terms(name: str, user: CurrentUser, limit: int = 50) -> dict[str, Any]:
    std = _standards()
    if name not in std.domains:
        raise HTTPException(404, "도메인이 없다")
    items = sorted((t for t in std.terms.values() if t.domain == name), key=lambda t: t.name)
    return {"total": len(items), "items": [t.__dict__ for t in items[:limit]]}


# ── 기관 추가 표준 (ADR-0009) ─────────────────────────


class LocalIn(BaseModel):
    kind: Literal["alias", "word", "term"]
    name: str = Field(min_length=1, max_length=60)
    abbr: str = ""
    english: str = ""
    description: str = ""
    is_format: bool = False
    domain_class: str = ""
    target: str = ""
    domain: str = ""


def _local() -> LocalStore:
    if state.local is None or state.base_standards is None:
        raise HTTPException(503, "시스템 DB 또는 공통표준이 없다")
    return state.local


@app.get("/api/standards/local")
async def local_list(user: CurrentUser) -> dict[str, Any]:
    """기관 추가 표준 목록. 적용되지 못한 항목(공통표준 새 판과 충돌 등)은 이유와 함께 준다."""
    rejected = {i.item_id: why for i, why in state.local_rejected}
    applied = {i.item_id: i for i in state.local_applied}
    rows = await _local().rows()
    for r in rows:
        r["rejected"] = rejected.get(r["item_id"])
        if r["item_id"] in applied:  # 정규화된 값(용어 약어 자동 생성 등)
            r["abbr"] = applied[r["item_id"]].abbr
    base = state.base_standards
    assert base is not None
    return {
        "items": rows, "kinds": KIND_LABEL, "version": base.version,
        "domain_classes": sorted({d.klass for d in base.domains.values()}),
        "domains": sorted(base.domains),
    }


def _checked(body: LocalIn, exclude: int | None = None) -> LocalItem:
    """원본·다른 기관 항목과 겹치지 않는지 확인한다. exclude: 수정 중인 항목은 빼고 본다."""
    base = state.base_standards
    assert base is not None
    others = [i for i in state.local_applied if i.item_id != exclude]
    std = apply_local(base, others).standards if exclude is not None else state.standards
    assert std is not None
    try:
        return check_item(std, LocalItem(**body.model_dump()), base)
    except LocalConflict as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/standards/local")
async def local_add(body: LocalIn, admin: AdminUser) -> dict[str, Any]:
    item = _checked(body)
    try:
        item_id = await _local().add(item, admin.user_id)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(400, f"이미 있는 {KIND_LABEL[item.kind]}: {item.name}") from exc
    await _reload_local()
    log.info("기관 표준 추가: %s %s by %s", item.kind, item.name, admin.username)
    return {"item_id": item_id, "abbr": item.abbr}


@app.put("/api/standards/local/{item_id}")
async def local_update(item_id: int, body: LocalIn, admin: AdminUser) -> dict[str, Any]:
    old = next((i for i in await _local().list() if i.item_id == item_id), None)
    if old is None:
        raise HTTPException(404, "항목이 없다")
    renamed = old.kind == "word" and (old.name != normalize_name(body.name) or old.abbr != body.abbr.strip().upper())
    if renamed and (deps := dependents(state.local_applied, state.standards, old.name)):  # type: ignore[arg-type]
        raise HTTPException(400, f"이 단어를 쓰는 항목이 있어 이름·약어를 바꿀 수 없다: {', '.join(deps)}")
    item = _checked(body, exclude=item_id)
    await _local().update(item_id, item)
    await _reload_local()
    return {"item_id": item_id, "abbr": item.abbr}


@app.delete("/api/standards/local/{item_id}")
async def local_delete(item_id: int, admin: AdminUser) -> dict[str, Any]:
    old = next((i for i in await _local().list() if i.item_id == item_id), None)
    if old is None:
        raise HTTPException(404, "항목이 없다")
    if old.kind == "word" and (deps := dependents(state.local_applied, state.standards, old.name)):  # type: ignore[arg-type]
        raise HTTPException(400, f"이 단어를 쓰는 항목을 먼저 지운다: {', '.join(deps)}")
    await _local().delete(item_id)
    await _reload_local()
    log.info("기관 표준 삭제: %s %s by %s", old.kind, old.name, admin.username)
    return {"ok": True}


@app.get("/api/standards/local.csv")
async def local_export(user: CurrentUser) -> Response:
    return Response(to_csv(await _local().list()), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=nlxpg_local_standards.csv"})


@app.post("/api/standards/local/import")
async def local_import(file: Annotated[UploadFile, File()], admin: AdminUser) -> dict[str, Any]:
    """CSV를 한 행씩 검증해 넣는다. 이미 같은 항목이 있으면 건너뛰고, 문제가 있는 행은 이유를 돌려준다."""
    try:
        items = from_csv((await file.read()).decode("utf-8-sig"))
    except (LocalConflict, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"CSV를 읽을 수 없다: {exc}") from exc
    assert state.base_standards is not None
    added, skipped, errors = await import_items(_local(), state.base_standards, items, admin.user_id)
    await _reload_local()
    log.info("기관 표준 불러오기: 추가 %d, 건너뜀 %d, 오류 %d by %s", added, skipped, len(errors), admin.username)
    return {"added": added, "skipped": skipped, "errors": errors}


class CheckIn(BaseModel):
    source: Literal["ddl", "db", "run"]
    ddl: str | None = None
    dsn: str | None = None
    db_schema: str = "public"
    run_id: int | None = None


@app.post("/api/standards/check")
async def standards_check(req: CheckIn, user: CurrentUser) -> dict[str, Any]:
    from nlxpg.standards.sources import columns_from_database, columns_from_ddl, columns_from_ir

    std = _standards()
    try:
        if req.source == "ddl":
            if not req.ddl or not state.settings.sandbox_pg_dsn:
                raise HTTPException(400, "DDL과 샌드박스 DSN이 필요하다")
            columns = await columns_from_ddl(state.settings.sandbox_pg_dsn, req.ddl)
        elif req.source == "db":
            if not req.dsn:
                raise HTTPException(400, "DB 접속 문자열이 필요하다")
            columns = await columns_from_database(req.dsn, req.db_schema)
        else:
            await _own_run(req.run_id or 0, user)
            run = await _store().get_run(req.run_id or 0)
            if not run or not run.get("schema_ir"):
                raise HTTPException(404, "스키마가 있는 실행 이력이 없다")
            columns = columns_from_ir(SchemaIR.model_validate(run["schema_ir"]))
    except HTTPException:
        raise
    except Exception as exc:  # DDL 오류·접속 실패를 화면에 그대로 보여준다
        msg = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
        target = mask_dsn(req.dsn) if req.source == "db" else req.source
        log.warning("표준 검증 실패 (%s, %s): %s", req.source, target, msg)
        raise HTTPException(400, msg) from exc
    report = check_columns(std, columns).to_dict()
    report["labels"] = VERDICT_LABEL
    return report
