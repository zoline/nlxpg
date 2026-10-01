"""표준 검증 입력: DB 카탈로그, DDL, nlxpg IR → ColumnInput 목록."""
from __future__ import annotations

from urllib.parse import urlsplit

import asyncpg

from nlxpg.ir import SchemaIR
from nlxpg.standards.check import ColumnInput, logical_from_comment
from nlxpg.validate.sandbox import run_in_sandbox

_COLUMNS = """
SELECT c.relname AS table_name, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(c.oid, a.attnum) AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


async def _read_catalog(conn: asyncpg.Connection, schema: str) -> list[ColumnInput]:
    rows = await conn.fetch(_COLUMNS, schema)
    return [
        ColumnInput(r["table_name"], r["column_name"], r["data_type"], logical_from_comment(r["comment"]))
        for r in rows
    ]


async def columns_from_database(dsn: str, schema: str = "public") -> list[ColumnInput]:
    """접속 실패는 원인과 대처법을 담은 ValueError로 바꾼다 — 화면에 그대로 보여주기 때문이다.
    실측(2026-10-01): 시간 초과는 'TimeoutError: '만, 비밀번호의 #·$ 미인코딩은
    "invalid literal for int()"만 나와서 사용자가 원인을 알 수 없었다."""
    host = urlsplit(dsn).hostname if "://" in dsn else None
    try:
        conn = await asyncpg.connect(dsn, timeout=15)
    except (TimeoutError, OSError) as exc:
        raise ValueError(f"DB 서버({host})에 접속할 수 없다 ({type(exc).__name__}). 호스트·포트를 확인한다.") from exc
    except asyncpg.InvalidPasswordError as exc:
        raise ValueError(f"비밀번호 인증 실패 (계정 {urlsplit(dsn).username}, 서버 {host}).") from exc
    except asyncpg.InvalidCatalogNameError as exc:
        raise ValueError(f"DB가 없다: {exc}") from exc
    except ValueError as exc:
        raise ValueError(
            "접속 문자열을 해석할 수 없다. 비밀번호에 #, $, @, /, : 같은 특수문자가 있으면 "
            f"URL 인코딩한다(예: # → %23, $ → %24). ({exc})"
        ) from exc
    try:
        columns = await _read_catalog(conn, schema)
        if not columns:
            schemas = [r[0] for r in await conn.fetch(
                "SELECT DISTINCT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1")]
            raise ValueError(
                f"스키마 '{schema}'에 테이블이 없다. 테이블이 있는 스키마: {', '.join(schemas) or '(없음)'}"
            )
        return columns
    finally:
        await conn.close()


async def columns_from_ddl(sandbox_dsn: str, ddl: str) -> list[ColumnInput]:
    """DDL을 샌드박스에서 실행해 카탈로그로 읽는다(항상 롤백). 직접 파싱하는 것보다
    타입 표기·COMMENT 해석이 PostgreSQL과 정확히 같다. 실행이 실패하면 ValueError."""
    result = await run_in_sandbox(sandbox_dsn, ddl, inspect=_read_catalog)
    if not result.ok:
        raise ValueError(f"DDL 실행 실패: {result.error}")
    return result.inspected


def columns_from_ir(ir: SchemaIR) -> list[ColumnInput]:
    return [
        ColumnInput(e.physical_name, a.physical_name, a.data_type, a.logical_name)
        for e in ir.entities for a in e.attributes
    ]
