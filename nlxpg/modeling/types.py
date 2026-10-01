"""데이터 타입 정규화.

추출 단계의 data_type_hint는 자유 문자열이다. 알 수 있는 PostgreSQL 타입이면 그대로 두고,
흔한 별칭은 정식 이름으로 바꾸고, 모르는 것은 text로 떨어뜨린다(샌드박스 실행 실패 방지).
값 예시 기반 추론(plan.md §핵심 기술 요소)은 infer_from_examples가 보조한다.
"""
from __future__ import annotations

import re

_SIMPLE = {
    "smallint", "integer", "bigint", "real", "double precision", "boolean", "date", "text",
    "timestamp", "timestamptz", "time", "interval", "uuid", "json", "jsonb", "bytea", "inet",
}
_ALIASES = {
    "int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint",
    "bool": "boolean", "float": "double precision", "float8": "double precision",
    "double": "double precision", "float4": "real", "string": "text", "str": "text",
    "datetime": "timestamp", "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp", "serial": "integer", "bigserial": "bigint",
    "money": "numeric(18,2)", "clob": "text", "blob": "bytea",
}
_PARAM = re.compile(
    r"^(varchar|character varying|char|character|numeric|decimal)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)$"
)
_BARE_PARAM = {"varchar": "text", "character varying": "text", "numeric": "numeric",
               "decimal": "numeric", "char": "char(1)", "character": "char(1)"}


def normalize_type(hint: str) -> str:
    t = re.sub(r"\s+", " ", hint.strip().lower())
    if not t:
        return "text"
    if t in _SIMPLE:
        return t
    if t in _ALIASES:
        return _ALIASES[t]
    if t in _BARE_PARAM:
        return _BARE_PARAM[t]
    m = _PARAM.match(t)
    if m:
        base, p, s = m.groups()
        base = {"character varying": "varchar", "character": "char", "decimal": "numeric"}.get(base, base)
        if base in ("varchar", "char"):
            return f"{base}({max(1, min(int(p), 10485760))})"
        return f"numeric({p},{s})" if s else f"numeric({p})"
    return "text"


_DATE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$")
_INT = re.compile(r"^[+-]?\d{1,18}$")
_DEC = re.compile(r"^[+-]?\d[\d,]*\.\d+$")


def infer_from_examples(examples: list[str]) -> str | None:
    vals = [v.strip() for v in examples if v and v.strip()]
    if not vals:
        return None
    if all(_DATE.match(v) for v in vals):
        return "date"
    if all(_INT.match(v.replace(",", "")) for v in vals):
        # 앞자리 0이 있는 값(우편번호, 코드)은 숫자가 아니라 문자열이다.
        if any(len(v) > 1 and v.startswith("0") for v in vals):
            return f"varchar({max(len(v) for v in vals)})"
        return "bigint" if any(len(v) > 9 for v in vals) else "integer"
    if all(_DEC.match(v) for v in vals):
        return "numeric"
    return None
