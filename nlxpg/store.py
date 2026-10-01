"""nlxpg 시스템 DB — 실행 이력, 산출 스키마, 평가·검토 결과.

pgxnl 시스템 DB와 같은 서버를 쓰되 DB는 분리한다(settings.system_pg_dsn 참고).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

import asyncpg

from nlxpg.ir import SchemaIR
from nlxpg.parsing import Document
from nlxpg.settings import PROJECT_ROOT, replace_dbname

SCHEMA_SQL = PROJECT_ROOT / "db" / "schema.sql"
#: 사람 검토 체크리스트 (plan.md §사람 검토 절차). nlxpg_reviews 컬럼과 같은 순서.
REVIEW_ITEMS = (
    "entity_completeness", "normalization", "keys_relationships", "data_types", "naming", "evidence",
)


@dataclass
class InitResult:
    database: str
    created: bool


async def init_database(dsn: str, *, maintenance_db: str = "postgres") -> InitResult:
    """DB가 없으면 만들고 스키마를 적용한다. 여러 번 실행해도 안전하다.

    CREATE DATABASE는 대상 DB가 아니라 같은 서버의 다른 DB에 붙어서 실행해야 한다.
    계정에 CREATEDB 권한이 없으면 DBA에게 DB 생성을 요청한 뒤 다시 실행한다
    (그 경우 스키마 적용만 한다).
    """
    dbname = urlsplit(dsn).path.lstrip("/")
    if not dbname:
        raise ValueError("DSN에 데이터베이스 이름이 없다")

    created = False
    admin = await asyncpg.connect(replace_dbname(dsn, maintenance_db))
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            # 식별자는 파라미터 바인딩이 안 된다 — quote_ident로 감싼다.
            quoted = await admin.fetchval("SELECT quote_ident($1)", dbname)
            await admin.execute(f"CREATE DATABASE {quoted}")
            created = True
    finally:
        await admin.close()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    return InitResult(database=dbname, created=created)


async def bootstrap(admin_dsn: str, dsn: str) -> list[str]:
    """관리자 계정으로 dsn의 계정·DB를 만들고 스키마를 적용한다(재실행 안전).

    계정 이름·비밀번호·DB 이름은 모두 dsn에서 읽는다. 비밀번호가 이미 있는 계정이면
    dsn의 비밀번호로 맞춘다 — .env와 서버의 비밀번호가 어긋나는 일을 막는다.
    admin_dsn은 저장하지 않는다.
    """
    parts = urlsplit(dsn)
    user, password = unquote(parts.username or ""), unquote(parts.password or "")
    dbname = parts.path.lstrip("/")
    if not (user and password and dbname):
        raise ValueError("dsn에 계정, 비밀번호, DB 이름이 모두 있어야 한다")

    done: list[str] = []
    admin = await asyncpg.connect(replace_dbname(admin_dsn, "postgres"))
    try:
        # 식별자·리터럴은 바인딩이 안 되는 DDL이라 서버의 format()으로 인용한다.
        exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", user)
        verb = "ALTER" if exists else "CREATE"
        sql = await admin.fetchval(f"SELECT format('{verb} ROLE %I LOGIN PASSWORD %L', $1::text, $2::text)", user, password)
        await admin.execute(sql)
        done.append(f"계정 {user} {'비밀번호 갱신' if exists else '생성'}")

        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname):
            sql = await admin.fetchval("SELECT format('CREATE DATABASE %I OWNER %I', $1::text, $2::text)", dbname, user)
            await admin.execute(sql)
            done.append(f"DB {dbname} 생성 (소유자 {user})")
        else:
            done.append(f"DB {dbname} 이미 있음")
    finally:
        await admin.close()

    await init_database(dsn)
    done.append("스키마 적용")
    return done


class RunStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> RunStore:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        assert pool is not None
        return cls(pool)

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    async def close(self) -> None:
        await self._pool.close()

    async def upsert_document(self, doc: Document, *, classification: str = "public") -> None:
        await self._pool.execute(
            """
            INSERT INTO nlxpg_documents
                (doc_id, title, source_path, media_type, content_sha256, classification, content)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (doc_id) DO UPDATE SET
                title = EXCLUDED.title, source_path = EXCLUDED.source_path,
                media_type = EXCLUDED.media_type, content_sha256 = EXCLUDED.content_sha256,
                classification = EXCLUDED.classification, content = EXCLUDED.content
            """,
            doc.doc_id, doc.title, doc.source_path, doc.media_type, doc.sha256, classification,
            doc.text,
        )

    async def start_run(
        self, doc_ids: list[str], *, label: str | None, provider: str, model: str,
        config: dict[str, Any], created_by: int | None = None,
    ) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            run_id: int = await conn.fetchval(
                "INSERT INTO nlxpg_runs (label, llm_provider, llm_model, config, created_by) "
                "VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING run_id",
                label, provider, model, json.dumps(config, ensure_ascii=False), created_by,
            )
            await conn.executemany(
                "INSERT INTO nlxpg_run_documents (run_id, doc_id) VALUES ($1, $2)",
                [(run_id, d) for d in doc_ids],
            )
        return run_id

    async def finish_run(
        self, run_id: int, *, ir: SchemaIR | None, ddl: str | None, ddl_valid: bool | None,
        validation: dict[str, Any] | None, error: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE nlxpg_runs SET
                status = CASE WHEN $6::text IS NULL THEN 'succeeded' ELSE 'failed' END,
                schema_ir = $2::jsonb, ddl = $3, ddl_valid = $4, validation = $5::jsonb,
                error = $6, finished_at = now()
            WHERE run_id = $1
            """,
            run_id,
            ir.model_dump_json() if ir else None,
            ddl, ddl_valid,
            json.dumps(validation, ensure_ascii=False) if validation is not None else None,
            error,
        )

    async def add_llm_call(self, run_id: int, model: str, rec: dict[str, Any]) -> None:
        usage = rec.get("usage") or {}
        await self._pool.execute(
            """
            INSERT INTO nlxpg_llm_calls (run_id, label, attempt, schema_name, model, system_prompt,
                messages, response, finish_reason, input_tokens, output_tokens, duration_ms, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13)
            """,
            run_id, rec.get("label"), rec.get("attempt", 0), rec.get("schema_name"), model,
            rec.get("system_prompt"), json.dumps(rec.get("messages"), ensure_ascii=False),
            rec.get("response"), rec.get("finish_reason"), usage.get("input_tokens"),
            usage.get("output_tokens"), rec.get("duration_ms"), rec.get("error"),
        )

    async def list_llm_calls(self, run_id: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM nlxpg_llm_calls WHERE run_id = $1 ORDER BY call_id", run_id)
        out = []
        for r in rows:
            d = dict(r)
            d["messages"] = json.loads(d["messages"]) if d["messages"] else []
            out.append(d)
        return out

    async def delete_run(self, run_id: int) -> dict[str, Any] | None:
        """실행 이력을 지운다. 검토·평가 결과는 FK CASCADE로 함께 지워진다. 이 실행만 쓰던
        문서(원문 포함)도 지운다 — 사내 문서 원문이 남지 않게. 없는 run_id면 None."""
        async with self._pool.acquire() as conn, conn.transaction():
            config = await conn.fetchval("SELECT config FROM nlxpg_runs WHERE run_id = $1", run_id)
            if config is None:
                return None
            doc_ids = [r["doc_id"] for r in await conn.fetch(
                "SELECT doc_id FROM nlxpg_run_documents WHERE run_id = $1", run_id)]
            reviews = await conn.fetchval("SELECT count(*) FROM nlxpg_reviews WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM nlxpg_runs WHERE run_id = $1", run_id)
            orphans = await conn.fetch(
                """
                DELETE FROM nlxpg_documents d
                WHERE d.doc_id = ANY($1::text[])
                  AND NOT EXISTS (SELECT 1 FROM nlxpg_run_documents r WHERE r.doc_id = d.doc_id)
                RETURNING doc_id, source_path
                """,
                doc_ids,
            )
        return {
            "config": json.loads(config) if isinstance(config, str) else config,
            "reviews": reviews,
            "deleted_documents": [dict(r) for r in orphans],
            "kept_documents": sorted(set(doc_ids) - {r["doc_id"] for r in orphans}),
        }

    async def documents_under(self, prefix: str) -> int:
        """source_path가 이 경로 아래인 문서 수 — 업로드 폴더를 지워도 되는지 확인할 때 쓴다."""
        return await self._pool.fetchval(
            "SELECT count(*) FROM nlxpg_documents WHERE source_path LIKE $1 || '%'", prefix
        )

    # ── 조회 (UI) ──────────────────────────────────────
    async def list_runs(self, limit: int = 100, *, owner: int | None = None) -> list[dict[str, Any]]:
        """owner가 주어지면 그 사용자가 만든 실행만 (일반 사용자). None이면 전부 (관리자)."""
        rows = await self._pool.fetch(
            """
            SELECT r.run_id, r.label, r.status, r.llm_model, r.ddl_valid, r.error,
                   r.started_at, r.finished_at, r.created_by,
                   (SELECT display_name FROM nlxpg_users u WHERE u.user_id = r.created_by) AS created_by_name,
                   r.config->>'standards_version' AS standards_version,
                   COALESCE(jsonb_array_length(r.schema_ir->'entities'), 0) AS table_count,
                   COALESCE(array_agg(d.title ORDER BY d.title) FILTER (WHERE d.doc_id IS NOT NULL),
                            '{}') AS documents,
                   (SELECT count(*) FROM nlxpg_reviews v WHERE v.run_id = r.run_id) AS review_count
            FROM nlxpg_runs r
            LEFT JOIN nlxpg_run_documents rd ON rd.run_id = r.run_id
            LEFT JOIN nlxpg_documents d ON d.doc_id = rd.doc_id
            WHERE $2::int IS NULL OR r.created_by = $2
            GROUP BY r.run_id
            ORDER BY r.run_id DESC
            LIMIT $1
            """,
            limit, owner,
        )
        return [dict(r) for r in rows]

    async def get_run(self, run_id: int) -> dict[str, Any] | None:
        row = await self._pool.fetchrow("SELECT * FROM nlxpg_runs WHERE run_id = $1", run_id)
        if row is None:
            return None
        run = dict(row)
        for key in ("config", "schema_ir", "validation"):
            if isinstance(run.get(key), str):
                run[key] = json.loads(run[key])
        docs = await self._pool.fetch(
            """
            SELECT d.doc_id, d.title, d.media_type, d.classification
            FROM nlxpg_run_documents rd JOIN nlxpg_documents d USING (doc_id)
            WHERE rd.run_id = $1 ORDER BY d.title
            """,
            run_id,
        )
        run["documents"] = [dict(d) for d in docs]
        return run

    async def run_owner(self, run_id: int) -> tuple[bool, int | None]:
        """(존재 여부, 만든 사용자). 접근 권한 확인용."""
        row = await self._pool.fetchrow("SELECT created_by FROM nlxpg_runs WHERE run_id = $1", run_id)
        return (row is not None, row["created_by"] if row else None)

    async def document_visible_to(self, doc_id: str, user_id: int) -> bool:
        """이 사용자가 만든 실행 중 하나라도 이 문서를 쓰는가."""
        return bool(await self._pool.fetchval(
            "SELECT 1 FROM nlxpg_run_documents rd JOIN nlxpg_runs r USING (run_id) "
            "WHERE rd.doc_id = $1 AND r.created_by = $2 LIMIT 1", doc_id, user_id))

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT doc_id, title, media_type, classification, content FROM nlxpg_documents "
            "WHERE doc_id = $1",
            doc_id,
        )
        return dict(row) if row else None

    async def add_review(self, run_id: int, review: dict[str, Any]) -> int:
        return await self._pool.fetchval(
            """
            INSERT INTO nlxpg_reviews (run_id, reviewer, entity_completeness, normalization,
                keys_relationships, data_types, naming, evidence, comment)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING review_id
            """,
            run_id, review["reviewer"], *(review.get(k) for k in REVIEW_ITEMS), review.get("comment"),
        )

    async def list_reviews(self, run_id: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM nlxpg_reviews WHERE run_id = $1 ORDER BY review_id DESC", run_id
        )
        return [dict(r) for r in rows]
