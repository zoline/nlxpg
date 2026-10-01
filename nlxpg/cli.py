"""nlxpg CLI.

    nlxpg config                 설정 확인 (비밀값 가림)
    nlxpg check                  LLM·DB 접속 확인
    nlxpg db init                시스템 DB 생성·스키마 적용
    nlxpg design 문서... -o out  문서 → DDL·ERD·설명서
    nlxpg validate schema.sql    DDL 샌드박스 실행
    nlxpg standards lookup 이름… 공통표준 적용 결과 조회
    nlxpg standards check --ddl|--dsn|--run   기존 스키마의 공통표준 준수 검증
    nlxpg normalize --run N | --ir schema.json   정규형 검사 (데이터 없이, 규칙 기반)
    nlxpg runs list | delete N   실행 이력 조회·삭제
    nlxpg users list | add | passwd | set   웹 UI 사용자 (관리자 비밀번호 복구도 여기서)
    nlxpg setup llm              LLM 접속 설정을 묻고 .env에 쓴 뒤 접속 시험
    nlxpg serve                  웹 UI (기본 http://localhost:8200)
    nlxpg evaluate pred.json --gold-dsn ... [--gold-schema public]
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from nlxpg.settings import PGXNL_ENV, load_settings, mask_dsn

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True, help="시스템 DB 관리")
app.add_typer(db_app, name="db")
setup_app = typer.Typer(no_args_is_help=True, help="설치 후 설정")
app.add_typer(setup_app, name="setup")
users_app = typer.Typer(no_args_is_help=True, help="사용자 (웹 UI 로그인)")
app.add_typer(users_app, name="users")
runs_app = typer.Typer(no_args_is_help=True, help="실행 이력")
app.add_typer(runs_app, name="runs")
std_app = typer.Typer(no_args_is_help=True, help="공공데이터 공통표준 (ADR-0005)")
app.add_typer(std_app, name="standards")


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
    reload: Annotated[bool, typer.Option(help="코드 변경 시 자동 재시작(개발용)")] = False,
) -> None:
    """웹 UI를 띄운다."""
    import uvicorn

    s = load_settings()
    uvicorn.run("nlxpg.api.app:app", host=host or s.api_host, port=port or s.api_port, reload=reload)


@app.command()
def config() -> None:
    """현재 설정. 값이 어디서 왔는지(pgxnl/.env 폴백 포함) 확인할 때 쓴다."""
    s = load_settings()
    typer.echo(f"pgxnl .env      : {PGXNL_ENV} ({'있음' if PGXNL_ENV.exists() else '없음'})")
    # LLM은 DB에서만 읽는다 (ADR-0007)
    async def roles():  # type: ignore[no-untyped-def]
        from nlxpg.llm.profiles import ProfileStore
        from nlxpg.store import RunStore

        store = await RunStore.connect(s.system_pg_dsn)
        try:
            return await ProfileStore(store.pool, s.secret_key or "-").roles()
        finally:
            await store.close()

    try:
        r = asyncio.run(roles()) if s.system_pg_dsn else {}
        for role, label in (("extract", "문서 추출"), ("testdoc", "테스트 문서")):
            v = r.get(role)
            shown = f"{v['name']} ({v['provider']}/{v['model']})" if v else (
                "(문서 추출과 같음)" if role == "testdoc" else "미설정 — 웹 UI 관리 → 모델 설정")
            typer.echo(f"llm {label:10}: {shown}")
    except Exception as exc:  # noqa: BLE001 — 진단 출력용
        typer.echo(f"llm             : 확인 못함 ({type(exc).__name__}: {exc})")
    typer.echo(f"secret key      : {'있음' if s.secret_key else '없음 (처음 쓸 때 생성)'}")
    typer.echo(f"embedding       : {s.embedding_model} @ {s.embedding_base_url} (enabled={s.embedding_enabled})")
    typer.echo(f"standards       : {s.standards_dir} (판 {s.standards_version or '최신'}, "
               f"적용={s.apply_standards})")
    typer.echo(f"system db       : {mask_dsn(s.system_pg_dsn)}")
    typer.echo(f"sandbox db      : {mask_dsn(s.sandbox_pg_dsn)}")


@app.command()
def check(
    llm: Annotated[str, typer.Option(help="확인할 LLM 역할: extract | testdoc")] = "extract",
) -> None:
    """LLM 구조화 출력 호출과 시스템 DB 접속을 실제로 시도한다."""
    if llm not in ("extract", "testdoc"):
        raise typer.BadParameter("--llm은 extract 또는 testdoc")
    asyncio.run(_check(llm))


async def _db_llm_settings(s, role: str):  # type: ignore[no-untyped-def]
    """역할의 LLM 설정을 DB 프로필에서 읽는다 (LLM은 DB에서만, llm/profiles.py)."""
    from nlxpg.llm.profiles import LLMNotConfigured, ProfileStore
    from nlxpg.settings import ensure_secret_key
    from nlxpg.store import RunStore

    if not s.system_pg_dsn:
        raise LLMNotConfigured("시스템 DB(NLXPG_SYSTEM_PG_DSN)가 없어 LLM 설정을 읽을 수 없다")
    store = await RunStore.connect(s.system_pg_dsn)
    try:
        return await ProfileStore(store.pool, ensure_secret_key(s)).settings_for(role, s)
    finally:
        await store.close()


async def _check(role: str) -> None:
    import asyncpg
    from pydantic import BaseModel

    from nlxpg.llm import create_llm

    s = load_settings()

    class Ping(BaseModel):
        answer: str

    from nlxpg.llm.profiles import LLMNotConfigured

    try:
        # 프로필이 없거나(LLMNotConfigured) 덜 채워졌으면(주소 없음 등) 여기서 막힌다. 그것도 FAIL로 보여준다.
        llm = create_llm(await _db_llm_settings(s, role))
    except (ValueError, LLMNotConfigured, OSError) as exc:
        typer.secho(f"LLM  FAIL {role}: {exc}", fg="red")
        llm = None
    if llm is not None:
        name = f"{role}: {llm.provider}/{llm.model}"
        try:
            r = await llm.complete_json("질문에 짧게 답한다.", '"ok"라고 answer 필드에 답하라.', Ping)
            typer.secho(f"LLM  OK   {name} guided={llm.guided_mode} → {r.answer!r}", fg="green")
        except Exception as exc:  # noqa: BLE001 — 진단 출력용
            typer.secho(f"LLM  FAIL {name} {type(exc).__name__}: {exc}", fg="red")
        finally:
            await llm.aclose()

    if not s.system_pg_dsn:
        typer.secho("DB   SKIP system_pg_dsn 미설정", fg="yellow")
        return
    try:
        conn = await asyncpg.connect(s.system_pg_dsn, timeout=10)
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'nlxpg\\_%'"
            )
            typer.secho(f"DB   OK   {mask_dsn(s.system_pg_dsn)} (nlxpg 테이블 {n}개)", fg="green")
        finally:
            await conn.close()
    except asyncpg.InvalidCatalogNameError:
        typer.secho(f"DB   MISSING {mask_dsn(s.system_pg_dsn)} — `nlxpg db init`으로 생성", fg="yellow")
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"DB   FAIL {type(exc).__name__}: {exc}", fg="red")


@setup_app.command("llm")
def setup_llm(
    role: Annotated[str, typer.Option(help="extract(문서 추출) | testdoc(테스트 문서 작성)")] = "extract",
    provider: Annotated[str | None, typer.Option(help="openai(vLLM 등 OpenAI 호환) | watsonx")] = None,
    model: Annotated[str | None, typer.Option()] = None,
    base_url: Annotated[str | None, typer.Option(help="예: https://host:8000/v1 (vLLM), https://cpd-host/ (watsonx)")] = None,
    verify_ssl: Annotated[bool | None, typer.Option("--verify-ssl/--no-verify-ssl", help="자체서명 인증서면 --no-verify-ssl")] = None,
    name: Annotated[str | None, typer.Option(help="프로필 이름 (기본: 제공자/모델)")] = None,
    project_id: Annotated[str | None, typer.Option(help="watsonx 프로젝트 ID")] = None,
    username: Annotated[str | None, typer.Option(help="watsonx(CPD) 계정")] = None,
    if_missing: Annotated[bool, typer.Option("--if-missing", help="이 역할에 이미 프로필이 있으면 아무것도 하지 않는다")] = False,
    test: Annotated[bool, typer.Option("--test/--no-test", help="저장한 뒤 접속 시험")] = True,
) -> None:
    """LLM 프로필을 DB에 저장하고 역할에 지정한다(웹 UI 관리 → 모델 설정과 같은 일). 화면을 못 쓰는 서버용.

    빠진 값은 화면에서 묻는다. 키·비밀번호는 명령줄에 쓰지 않는다(프로세스 목록에 보인다) —
    숨김 입력으로 받거나, 자동 설치라면 환경변수 NLXPG_SETUP_SECRET으로 넘긴다.
    """
    import os
    import subprocess
    import sys

    if role not in ("extract", "testdoc"):
        raise typer.BadParameter("role은 extract 또는 testdoc")
    s = load_settings()
    if not s.system_pg_dsn:
        raise typer.BadParameter("시스템 DB(NLXPG_SYSTEM_PG_DSN)가 먼저 필요하다 — LLM 설정은 DB에 저장된다")

    async def roles():  # type: ignore[no-untyped-def]
        from nlxpg.llm.profiles import ProfileStore
        from nlxpg.settings import ensure_secret_key
        from nlxpg.store import RunStore

        store = await RunStore.connect(s.system_pg_dsn)
        try:
            return await ProfileStore(store.pool, ensure_secret_key(s)).roles()
        finally:
            await store.close()

    current = asyncio.run(roles())
    if if_missing and role in current:
        c = current[role]
        typer.echo(f"LLM({role}) 이미 설정됨: {c['name']} ({c['provider']}/{c['model']}) — 건너뜀")
        return

    interactive = sys.stdin.isatty()

    def ask(value, label, default=None, choices=None):  # type: ignore[no-untyped-def]
        if value is not None:
            return value
        if not interactive:
            if default is None:
                raise typer.BadParameter(f"{label} 값이 필요하다 (화면 입력이 없는 실행)")
            return default
        while True:
            v = typer.prompt(label, default=default)
            if not choices or v in choices:
                return v
            typer.echo(f"  {' | '.join(choices)} 중 하나")

    provider = ask(provider, "제공자 (openai=vLLM 등 OpenAI 호환 | watsonx)", "openai", ["openai", "watsonx"])
    model = ask(model, "모델")
    base_url = ask(base_url, "주소")
    if verify_ssl is None:
        verify_ssl = typer.confirm("인증서를 검증할까요? (자체서명이면 n)", default=True) if interactive else True
    data: dict = {"name": name or f"{provider}/{model}", "provider": provider, "model": model,
                  "base_url": base_url, "verify_ssl": verify_ssl}
    if provider == "watsonx":
        data["watsonx_project_id"] = ask(project_id, "watsonx 프로젝트 ID")
        data["watsonx_username"] = ask(username, "watsonx(CPD) 계정 (IBM Cloud면 Enter)", "") or None

    secret = os.environ.get("NLXPG_SETUP_SECRET")
    if secret is None and interactive:
        label = "API 키 (없으면 Enter)" if provider == "openai" else "watsonx 비밀번호 (IBM Cloud API 키면 그 키)"
        secret = typer.prompt(label, default="", hide_input=True, show_default=False)
    if secret:
        # CPD 계정이면 비밀번호, 아니면(IBM Cloud·vLLM) API 키
        data["password" if provider == "watsonx" and data.get("watsonx_username") else "api_key"] = secret

    async def save():  # type: ignore[no-untyped-def]
        from nlxpg.llm.profiles import ProfileStore
        from nlxpg.settings import ensure_secret_key
        from nlxpg.store import RunStore

        store = await RunStore.connect(s.system_pg_dsn)
        try:
            ps = ProfileStore(store.pool, ensure_secret_key(s))
            existing = next((p for p in await ps.list() if p["name"] == data["name"]), None)
            pid = await ps.save(data, existing["profile_id"] if existing else None)
            await ps.set_role(role, pid)  # type: ignore[arg-type]
            return pid, existing is not None
        finally:
            await store.close()

    pid, updated = asyncio.run(save())
    typer.secho(f"프로필 '{data['name']}'({'수정' if updated else '생성'}, id {pid})을 {role} 역할에 지정했다", fg="green")
    if test:
        # 다른 프로세스로 시험한다 (check와 같은 경로)
        subprocess.run([sys.executable, "-m", "nlxpg.cli", "check", "--llm", role], check=False)


async def _with_users(fn):  # type: ignore[no-untyped-def]
    from nlxpg.auth import UserStore
    from nlxpg.store import RunStore

    store = await RunStore.connect(load_settings().system_pg_dsn)
    try:
        return await fn(UserStore(store.pool))
    finally:
        await store.close()


@users_app.command("list")
def users_list() -> None:
    """사용자 목록."""
    for u in asyncio.run(_with_users(lambda us: us.list())):
        state = "" if u["enabled"] else " [사용 중지]"
        change = " [비밀번호 변경 필요]" if u["must_change_password"] else ""
        typer.echo(f"{u['username']:16} {u['role']:6} {u['display_name']}{state}{change} "
                   f"실행 {u['run_count']} · 마지막 로그인 {u['last_login_at'] or '-'}")


@users_app.command("add")
def users_add(
    username: str,
    name: Annotated[str, typer.Option("--name", help="표시 이름")] = "",
    admin: Annotated[bool, typer.Option("--admin", help="관리자로 만든다")] = False,
    email: Annotated[str | None, typer.Option()] = None,
) -> None:
    """사용자를 만든다. 임시 비밀번호를 출력하고, 첫 로그인 때 바꾸게 한다."""
    from nlxpg.auth import temp_password

    password = temp_password()

    async def go(us):  # type: ignore[no-untyped-def]
        return await us.create(username, name or username, password, role="admin" if admin else "user",
                               email=email, must_change=True)

    u = asyncio.run(_with_users(go))
    typer.secho(f"{u.username} ({u.role}) 생성. 임시 비밀번호: {password}  (첫 로그인 때 바꿔야 한다)", fg="green")


@users_app.command("passwd")
def users_passwd(username: str) -> None:
    """비밀번호를 새로 정한다(관리자 비밀번호를 잊었을 때). 다음 로그인 때 바꾸게 한다."""
    from nlxpg.auth import check_password_policy

    password = typer.prompt("새 비밀번호", hide_input=True, confirmation_prompt=True)
    if problem := check_password_policy(password):
        raise typer.BadParameter(problem)

    async def go(us):  # type: ignore[no-untyped-def]
        row = next((u for u in await us.list() if u["username"] == username), None)
        if row is None:
            raise typer.BadParameter(f"사용자가 없다: {username}")
        await us.set_password(row["user_id"], password, must_change=True)
        await us.delete_user_sessions(row["user_id"])

    asyncio.run(_with_users(go))
    typer.secho(f"{username}: 비밀번호를 바꿨다. 로그인 세션을 모두 끊었다.", fg="green")


@users_app.command("set")
def users_set(
    username: str,
    role: Annotated[str | None, typer.Option(help="admin | user")] = None,
    enabled: Annotated[bool | None, typer.Option("--enable/--disable")] = None,
) -> None:
    """역할을 바꾸거나 사용 중지·재개한다."""
    if role not in (None, "admin", "user"):
        raise typer.BadParameter("role은 admin 또는 user")

    async def go(us):  # type: ignore[no-untyped-def]
        row = next((u for u in await us.list() if u["username"] == username), None)
        if row is None:
            raise typer.BadParameter(f"사용자가 없다: {username}")
        return await us.update(row["user_id"], role=role, enabled=enabled)

    u = asyncio.run(_with_users(go))
    typer.secho(f"{u.username}: 역할 {u.role}, {'사용' if u.enabled else '사용 중지'}", fg="green")


@runs_app.command("list")
def runs_list(limit: Annotated[int, typer.Option()] = 30) -> None:
    """실행 이력 목록."""
    async def go():  # type: ignore[no-untyped-def]
        from nlxpg.store import RunStore

        store = await RunStore.connect(load_settings().system_pg_dsn)
        try:
            return await store.list_runs(limit)
        finally:
            await store.close()

    for r in asyncio.run(go()):
        valid = {True: "DDL통과", False: "DDL실패", None: ""}[r["ddl_valid"]]
        typer.echo(f"#{r['run_id']:<4} {r['status']:9} {valid:7} 테이블 {r['table_count']:<3} "
                   f"검토 {r['review_count']:<2} {r['label'] or ''} [{', '.join(r['documents'])}]")


@runs_app.command("delete")
def runs_delete(
    run_ids: Annotated[list[int], typer.Argument(help="지울 run_id (여러 개 가능)")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="확인 없이 지운다")] = False,
) -> None:
    """실행 이력을 지운다. 검토 점수, 이 실행만 쓰던 원문·업로드 파일도 함께 지운다."""
    if not yes:
        typer.confirm(f"run {', '.join(map(str, run_ids))}을(를) 지웁니다. 되돌릴 수 없습니다. 계속할까요?",
                      abort=True)

    async def go():  # type: ignore[no-untyped-def]
        from nlxpg.service import delete_run
        from nlxpg.store import RunStore

        s = load_settings()
        store = await RunStore.connect(s.system_pg_dsn)
        try:
            return [(rid, await delete_run(store, s, rid)) for rid in run_ids]
        finally:
            await store.close()

    for rid, r in asyncio.run(go()):
        if r is None:
            typer.secho(f"#{rid}: 없음", fg="yellow")
            continue
        typer.secho(f"#{rid}: 삭제 (검토 {r['reviews']}건, 문서 삭제 {len(r['deleted_documents'])} · "
                    f"유지 {len(r['kept_documents'])}, 파일 {len(r['removed_files'])})", fg="green")


@db_app.command("init")
def db_init() -> None:
    """시스템 DB가 없으면 만들고 db/schema.sql을 적용한다(재실행 안전)."""
    from nlxpg.store import init_database

    s = load_settings()
    if not s.system_pg_dsn:
        raise typer.BadParameter("NLXPG_SYSTEM_PG_DSN 또는 PGXNL_SYSTEM_PG_DSN이 필요하다")
    typer.echo(f"대상: {mask_dsn(s.system_pg_dsn)}")
    r = asyncio.run(init_database(s.system_pg_dsn))
    typer.secho(f"{r.database}: {'생성 후 ' if r.created else ''}스키마 적용 완료", fg="green")


@db_app.command("bootstrap")
def db_bootstrap(
    admin_dsn: Annotated[str, typer.Option(
        envvar="NLXPG_ADMIN_PG_DSN", prompt="관리자 DSN (postgresql://postgres:...@host:5432/postgres)",
        hide_input=True, help="계정·DB 생성 권한이 있는 계정. 저장하지 않는다.",
    )],
) -> None:
    """시스템 DB용 계정과 DB를 만들고 스키마를 적용한다. 계정·비밀번호·DB 이름은 NLXPG_SYSTEM_PG_DSN에서 읽는다."""
    from nlxpg.store import bootstrap

    s = load_settings()
    if not s.system_pg_dsn:
        raise typer.BadParameter("NLXPG_SYSTEM_PG_DSN이 필요하다")
    typer.echo(f"대상: {mask_dsn(s.system_pg_dsn)}")
    for line in asyncio.run(bootstrap(admin_dsn, s.system_pg_dsn)):
        typer.secho(f"  {line}", fg="green")


def _load_standards(s):  # type: ignore[no-untyped-def]
    from nlxpg.standards import load_standards

    return load_standards(s.standards_dir, s.standards_version) if s.apply_standards else None


@std_app.command("lookup")
def standards_lookup(
    names: Annotated[list[str], typer.Argument(help="속성 논리명 (예: 거래처명 주문일자)")],
    entity: Annotated[bool, typer.Option("--entity", help="엔터티(테이블)명으로 조회")] = False,
    hint: Annotated[str, typer.Option(help="타입 힌트 (예: varchar(50))")] = "",
) -> None:
    """이름을 공통표준에 비추어 물리명·도메인·타입을 보여준다."""
    from nlxpg.standards import load_standards, resolve_attribute, resolve_entity

    s = load_settings()
    std = load_standards(s.standards_dir, s.standards_version)
    typer.echo(f"공통표준 {std.version}판")
    for name in names:
        r = resolve_entity(std, name) if entity else resolve_attribute(std, name, type_hint=hint)
        color = {"common": "green", "composed": "yellow"}.get(r.status, "red")
        typer.secho(
            f"  {name} → [{r.status}] {r.logical_name} {r.physical_name or '-'} "
            f"{r.domain or ''} {r.data_type or ''}".rstrip(), fg=color,
        )
        if r.words:
            typer.echo(f"      단어: {' + '.join(r.words)}")
        for note in r.notes:
            typer.echo(f"      {note}")


@std_app.command("check")
def standards_check(
    ddl: Annotated[Path | None, typer.Option(exists=True, dir_okay=False, help="DDL 파일")] = None,
    dsn: Annotated[str | None, typer.Option(help="검증할 DB 접속 문자열")] = None,
    schema: Annotated[str, typer.Option(help="--dsn과 함께: 스키마")] = "public",
    run: Annotated[int | None, typer.Option(help="nlxpg 실행 이력 run_id")] = None,
    csv_out: Annotated[Path | None, typer.Option("--csv", help="매핑정보 CSV로 저장")] = None,
) -> None:
    """기존 스키마의 공통표준 준수를 컬럼 단위로 판정한다 (매뉴얼 [표 Ⅲ-2] 형식)."""
    import csv

    from nlxpg.standards import load_standards
    from nlxpg.standards.check import MAPPING_HEADER, VERDICT_LABEL, check_columns, mapping_rows

    if sum(x is not None for x in (ddl, dsn, run)) != 1:
        raise typer.BadParameter("--ddl, --dsn, --run 중 하나만 지정한다")
    s = load_settings()
    std = load_standards(s.standards_dir, s.standards_version)
    columns = asyncio.run(_check_inputs(s, ddl, dsn, schema, run))
    report = check_columns(std, columns)

    typer.echo(f"공통표준 {std.version}판, 컬럼 {len(report.columns)}개, 준수율 {report.compliance:.1%}")
    for verdict, n in report.summary.items():
        if n:
            typer.echo(f"  {VERDICT_LABEL[verdict]}: {n}")
    if csv_out:
        with csv_out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(MAPPING_HEADER)
            w.writerows(mapping_rows(report))
        typer.echo(f"→ {csv_out}")
    else:
        for row in mapping_rows(report):
            if row[4] != VERDICT_LABEL["standard"]:
                typer.echo("  " + " | ".join(x for x in row if x))


async def _check_inputs(s, ddl, dsn, schema, run):  # type: ignore[no-untyped-def]
    from nlxpg.standards.sources import columns_from_database, columns_from_ddl, columns_from_ir

    if ddl is not None:
        if not s.sandbox_pg_dsn:
            raise typer.BadParameter("DDL 검증에는 샌드박스 DSN이 필요하다")
        return await columns_from_ddl(s.sandbox_pg_dsn, ddl.read_text(encoding="utf-8"))
    if dsn is not None:
        return await columns_from_database(dsn, schema)

    from nlxpg.ir import SchemaIR
    from nlxpg.store import RunStore

    store = await RunStore.connect(s.system_pg_dsn)
    try:
        r = await store.get_run(run)
    finally:
        await store.close()
    if not r or not r.get("schema_ir"):
        raise typer.BadParameter(f"run {run}에 스키마가 없다")
    return columns_from_ir(SchemaIR.model_validate(r["schema_ir"]))


@app.command()
def normalize(
    run: Annotated[int | None, typer.Option(help="nlxpg 실행 이력 run_id")] = None,
    ir: Annotated[Path | None, typer.Option(exists=True, dir_okay=False, help="schema.json")] = None,
) -> None:
    """설계된 스키마의 정규형(1NF·2NF·3NF)과 파생 속성을 검사한다. 데이터 없이 이름·FK·값 예시로 판단한다."""
    from nlxpg.ir import SchemaIR
    from nlxpg.validate.normalform import check_normal_forms

    if (run is None) == (ir is None):
        raise typer.BadParameter("--run 또는 --ir 중 하나를 지정한다")
    s = load_settings()
    if ir is not None:
        schema = SchemaIR.model_validate_json(ir.read_text(encoding="utf-8"))
    else:
        schema = asyncio.run(_load_run_ir(s, run))
    findings = check_normal_forms(schema, _load_standards(s))
    _print_normal_forms(findings)


async def _load_run_ir(s, run_id):  # type: ignore[no-untyped-def]
    from nlxpg.ir import SchemaIR
    from nlxpg.store import RunStore

    store = await RunStore.connect(s.system_pg_dsn)
    try:
        r = await store.get_run(run_id)
    finally:
        await store.close()
    if not r or not r.get("schema_ir"):
        raise typer.BadParameter(f"run {run_id}에 스키마가 없다")
    return SchemaIR.model_validate(r["schema_ir"])


def _print_normal_forms(findings) -> None:  # type: ignore[no-untyped-def]
    v = sum(f.level == "violation" for f in findings)
    typer.echo(f"정규형: 위반 {v}, 확인 필요 {len(findings) - v}")
    for f in findings:
        color = "red" if f.level == "violation" else "yellow"
        label = "위반" if f.level == "violation" else "확인"
        typer.secho(f"  [{label} {f.normal_form}] {f.table}.{','.join(f.columns)}: {f.title}", fg=color)
        typer.echo(f"      {f.detail}")
        typer.echo(f"      → {f.suggestion}")


@app.command()
def design(
    documents: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False)],
    out: Annotated[Path, typer.Option("-o", "--out")] = Path("out"),
    label: Annotated[str | None, typer.Option(help="실행 이력에 남길 이름")] = None,
    sandbox: Annotated[bool, typer.Option(help="DDL을 샌드박스에서 실행해 검증")] = True,
    store: Annotated[bool, typer.Option(help="시스템 DB에 실행 이력 저장")] = True,
    internal: Annotated[bool, typer.Option(help="사내 문서(보안 구분 internal)로 기록")] = False,
    debug: Annotated[bool, typer.Option(help="LLM 호출(프롬프트·응답·토큰·시간·오류)을 시스템 DB와 out/llm_calls.json에 남긴다")] = False,
) -> None:
    """문서에서 스키마를 설계해 out/에 schema.json, schema.sql, erd.mmd, schema.md를 쓴다."""
    asyncio.run(_design(documents, out, label, sandbox, store, internal, debug))


async def _design(
    paths: list[Path], out: Path, label: str | None, sandbox: bool, use_store: bool, internal: bool,
    debug: bool = False,
) -> None:
    from nlxpg.parsing import load_document
    from nlxpg.service import RunService, validation_dict

    s = load_settings()
    docs = [load_document(p) for p in paths]
    llm_settings = await _db_llm_settings(s, "extract")
    store = None
    if use_store and s.system_pg_dsn:
        from nlxpg.store import RunStore

        store = await RunStore.connect(s.system_pg_dsn)
    if debug and store is None:
        typer.secho("--debug는 시스템 DB에 기록한다 — --no-store이거나 DB가 없어 기록하지 않는다", fg="yellow")
    run_id = None
    try:
        service = RunService(s, _load_standards(s), store, llm_settings)
        run_id = await service.start(docs, label=label, internal=internal,
                                     extra_config={"debug": debug} if debug else None)
        result = await service.execute(run_id, docs, sandbox=sandbox, debug=debug)
    finally:
        if store:
            if debug and run_id is not None:  # 실패해도 남긴다 — 디버그가 가장 필요한 경우
                calls = await store.list_llm_calls(run_id)
                out.mkdir(parents=True, exist_ok=True)
                (out / "llm_calls.json").write_text(
                    json.dumps(calls, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                typer.echo(f"LLM 호출 {len(calls)}건 → {out}/llm_calls.json")
            await store.close()
    validation = validation_dict(result)

    out.mkdir(parents=True, exist_ok=True)
    (out / "extracted.json").write_text(result.extracted.model_dump_json(indent=2), encoding="utf-8")
    (out / "schema.json").write_text(result.schema.model_dump_json(indent=2), encoding="utf-8")
    (out / "schema.sql").write_text(result.ddl, encoding="utf-8")
    (out / "erd.mmd").write_text(result.erd, encoding="utf-8")
    (out / "schema.md").write_text(result.description, encoding="utf-8")
    (out / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2),
                                         encoding="utf-8")

    typer.echo(f"테이블 {len(result.schema.entities)}개, 관계 {len(result.schema.relationships)}개 → {out}/")
    if run_id is not None:
        typer.echo(f"run_id {run_id}")
    for i in result.lint:
        typer.secho(f"  [{i.level}] {i.target}: {i.message}",
                    fg="red" if i.level == "error" else "yellow")
    if result.normal_forms:
        _print_normal_forms(result.normal_forms)
    if result.sandbox:
        if result.sandbox.ok:
            typer.secho(f"샌드박스 통과 ({len(result.sandbox.tables_created)}개 테이블)", fg="green")
        else:
            typer.secho(f"샌드박스 실패: {result.sandbox.error}", fg="red")


@app.command()
def validate(ddl_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """DDL 파일을 샌드박스에서 실행해 본다(항상 롤백)."""
    from nlxpg.validate import run_in_sandbox

    s = load_settings()
    if not s.sandbox_pg_dsn:
        raise typer.BadParameter("샌드박스 DSN이 없다")
    r = asyncio.run(run_in_sandbox(s.sandbox_pg_dsn, ddl_file.read_text(encoding="utf-8")))
    if r.ok:
        typer.secho(f"통과: {', '.join(r.tables_created)}", fg="green")
    else:
        typer.secho(f"실패: {r.error}", fg="red")
        raise typer.Exit(1)


@app.command()
def evaluate(
    pred: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="schema.json")],
    gold_dsn: Annotated[str, typer.Option(help="정답 스키마가 있는 DB (예: BIRD 이관 DB)")],
    gold_schema: Annotated[str, typer.Option()] = "public",
) -> None:
    """생성 스키마를 정답 DB 스키마와 비교한다(현재 매처: 정규화 문자열 일치)."""
    from nlxpg.evaluation import schema_from_database, score_schema
    from nlxpg.ir import SchemaIR

    p = SchemaIR.model_validate_json(pred.read_text(encoding="utf-8"))
    g = asyncio.run(schema_from_database(gold_dsn, gold_schema))
    typer.echo(json.dumps(score_schema(p, g).to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
