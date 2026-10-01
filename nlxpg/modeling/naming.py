"""물리명 정규화.

명명 표준(사내 표준용어사전 또는 공공데이터 공통표준용어)은 아직 정하지 않았다
(docs/adr/README.md 결정 대기). 표준이 정해지면 논리명 → 물리명 매핑을 여기서 적용한다.
지금은 PostgreSQL 식별자로 안전하게 쓸 수 있도록만 다듬는다.
"""
from __future__ import annotations

import re

# 따옴표 없이 테이블·컬럼명으로 쓸 수 없는 PostgreSQL 예약어.
# PostgreSQL 16 `pg_get_keywords()`의 catcode R(reserved) 78개 + T(reserved, can be function or
# type name) 23개 전체다. 처음에는 R만 손으로 옮겨 적어 T가 빠졌고, '좋아요' 테이블이 `like`가
# 되어 DDL이 실패했다(2026-10-01, run 8). 목록을 바꿀 때는 서버에서 다시 뽑는다:
#   SELECT word FROM pg_get_keywords() WHERE catcode::text IN ('R', 'T') ORDER BY word;
RESERVED = frozenset({
    # R: reserved
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "both",
    "case", "cast", "check", "collate", "column", "constraint", "create", "current_catalog",
    "current_date", "current_role", "current_time", "current_timestamp", "current_user",
    "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "false",
    "fetch", "for", "foreign", "from", "grant", "group", "having", "in", "initially",
    "intersect", "into", "lateral", "leading", "limit", "localtime", "localtimestamp", "not",
    "null", "offset", "on", "only", "or", "order", "placing", "primary", "references",
    "returning", "select", "session_user", "some", "symmetric", "system_user", "table", "then",
    "to", "trailing", "true", "union", "unique", "user", "using", "variadic", "when", "where",
    "window", "with",
    # T: reserved (can be function or type name) — 테이블·컬럼명으로는 못 쓴다
    "authorization", "binary", "collation", "concurrently", "cross", "current_schema", "freeze",
    "full", "ilike", "inner", "is", "isnull", "join", "left", "like", "natural", "notnull",
    "outer", "overlaps", "right", "similar", "tablesample", "verbose",
})
MAX_LEN = 63  # NAMEDATALEN - 1


def to_identifier(name: str, *, fallback: str = "col") -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"{fallback}_{s}"
    if s in RESERVED:
        s = f"{s}_"
    return s[:MAX_LEN]


def unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    i = 2
    while f"{name[: MAX_LEN - 3]}_{i}" in taken:
        i += 1
    return f"{name[: MAX_LEN - 3]}_{i}"


def surrogate_key_name(table: str) -> str:
    """{table}_id. 예약어 회피용 접미사(order_ → order)는 떼고 붙인다 — order__id가 아니라 order_id."""
    return f"{table.rstrip('_')}_id"[:MAX_LEN]
