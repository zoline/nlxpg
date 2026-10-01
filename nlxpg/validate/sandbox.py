"""샌드박스 PostgreSQL에서 DDL을 실제로 실행해 본다.

일회용 스키마를 만들고 그 안에서 DDL을 실행한 뒤 **항상 롤백**한다. 성공해도 아무것도
남지 않으므로 공유 서버에서 돌려도 안전하다. PostgreSQL은 DDL도 트랜잭션으로 묶인다.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg

#: 롤백 전에 샌드박스 스키마를 들여다보는 함수 (conn, schema) → 결과
InspectFn = Callable[[asyncpg.Connection, str], Awaitable[Any]]


@dataclass
class SandboxResult:
    ok: bool
    error: str | None = None
    tables_created: list[str] = field(default_factory=list)
    inspected: Any = None


class _Rollback(Exception):
    pass


async def run_in_sandbox(
    dsn: str, ddl: str, *, timeout: float = 30.0, inspect: InspectFn | None = None
) -> SandboxResult:
    schema = f"nlxpg_sandbox_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(dsn, timeout=timeout)
    result = SandboxResult(ok=False)
    try:
        try:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL statement_timeout = '{int(timeout * 1000)}'")
                await conn.execute(f"CREATE SCHEMA {schema}")
                await conn.execute(f"SET LOCAL search_path = {schema}")
                try:
                    await conn.execute(ddl)
                except asyncpg.PostgresError as exc:
                    result.error = _format_error(exc)
                else:
                    rows = await conn.fetch(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = $1 ORDER BY table_name", schema,
                    )
                    result.ok = True
                    result.tables_created = [r["table_name"] for r in rows]
                    if inspect is not None:
                        result.inspected = await inspect(conn, schema)
                raise _Rollback
        except _Rollback:
            pass
    finally:
        await conn.close()
    return result


def _format_error(exc: asyncpg.PostgresError) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    detail = getattr(exc, "detail", None)
    if detail:
        parts.append(f"DETAIL: {detail}")
    return "\n".join(parts)
