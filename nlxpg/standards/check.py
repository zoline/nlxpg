"""기존 스키마의 공통표준 준수 검증.

매뉴얼의 "비표준 데이터 매핑정보"(Ⅲ.1.3, [표 Ⅲ-1]·[표 Ⅲ-2], 16~17쪽)와 같은 방식이다.
표준 여부는 한글 컬럼명, 영문 컬럼명, 데이터 타입, 데이터 길이 네 항목으로 판정한다.

1. 표준용어 찾기: 한글명(COMMENT)이 있으면 한글명으로(resolve_attribute),
   없으면 영문 컬럼명을 표준용어 영문약어명과 대조한다(유효 용어의 약어는 유일하다).
2. 찾은 표준과 영문명·타입·길이를 비교한다.

타입은 ADR-0005의 PostgreSQL 타입(연월일 → date 등)과 공통표준 원형(char(8) 등)을
모두 준수로 본다. 어느 쪽인지는 note에 남긴다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from nlxpg.standards.english import infer_words
from nlxpg.standards.loader import Domain, Standards
from nlxpg.standards.resolve import pg_type, resolve_attribute

Verdict = Literal[
    "standard",        # 영문명·타입·길이 모두 표준
    "type_mismatch",   # 영문명은 표준, 타입·길이가 다름
    "name_mismatch",   # 한글명으로 표준용어를 찾았으나 영문명이 다름 (매핑 필요)
    "composed",        # 표준용어는 없고 표준단어 조합으로 만들 수 있음 (DB표준 후보)
    "local",           # 기관 추가 표준(ADR-0009)으로 영문명이 맞음
    "nonstandard",     # 표준단어로도 분할되지 않음
    "unknown",         # 한글명이 없고 영문명도 표준 약어가 아님
]
VERDICT_LABEL = {
    "standard": "표준", "type_mismatch": "타입 불일치", "name_mismatch": "영문명 불일치",
    "composed": "조합(DB표준 후보)", "local": "기관 표준", "nonstandard": "비표준", "unknown": "판정 불가",
}


@dataclass
class ColumnInput:
    table: str
    column: str
    data_type: str  # PostgreSQL 표기 (format_type 결과 또는 DDL 표기)
    logical_name: str | None = None  # 한글 컬럼명. COMMENT에서 얻는다


@dataclass
class ColumnCheck:
    table: str
    column: str
    data_type: str
    logical_name: str | None
    verdict: Verdict
    #: logical: 한글명, physical: 영문명이 표준 약어와 같음, english: 영문 이름으로 추정
    matched_by: Literal["logical", "physical", "english", ""] = ""
    std_term: str | None = None
    std_physical: str | None = None
    std_domain: str | None = None
    std_type: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    standards_version: str
    columns: list[ColumnCheck]

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(c.verdict for c in self.columns)
        return {v: counts.get(v, 0) for v in VERDICT_LABEL}

    @property
    def compliance(self) -> float:
        """표준 준수율 = standard / 전체."""
        return round(self.summary["standard"] / len(self.columns), 4) if self.columns else 0.0

    def to_dict(self) -> dict:
        return {
            "standards_version": self.standards_version,
            "compliance": self.compliance,
            "summary": self.summary,
            "columns": [asdict(c) for c in self.columns],
        }


# ── 타입 비교 ────────────────────────────────────────────

_PG_ALIASES = {
    "character varying": "varchar", "character": "char", "bpchar": "char",
    "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
    "time without time zone": "time", "decimal": "numeric", "int4": "integer", "int8": "bigint",
}
_TYPE = re.compile(r"^\s*([a-z ]+?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$")


def normalize_pg_type(t: str) -> str:
    """'character varying(100)' → 'varchar(100)', 'numeric(15,0)' → 'numeric(15)'."""
    m = _TYPE.match(t.lower())
    if not m:
        return t.lower().strip()
    base, p, s = m.group(1).strip(), m.group(2), m.group(3)
    base = _PG_ALIASES.get(base, base)
    if base == "char" and p is None:
        p = "1"
    if s == "0":
        s = None
    if p is None:
        return base
    return f"{base}({p},{s})" if s else f"{base}({p})"


def _original_type(domain: Domain) -> str:
    """공통표준 원형 타입 (ADR-0005의 날짜 변환을 하지 않은 것). 연월일C8 → char(8)."""
    return pg_type(domain, adapt_dates=False)


def _type_ok(actual: str, domain: Domain) -> tuple[bool, str]:
    got = normalize_pg_type(actual)
    adr, orig = normalize_pg_type(pg_type(domain)), normalize_pg_type(_original_type(domain))
    if got == adr:
        return True, ""
    if got == orig:
        return True, "공통표준 원형 타입 (ADR-0005 변환 전)"
    return False, f"타입 {got} ≠ 표준 {adr}" + (f" (원형 {orig})" if orig != adr else "")


# ── 검증 ────────────────────────────────────────────────


def check_columns(std: Standards, columns: list[ColumnInput]) -> CheckReport:
    by_abbr = {t.abbr.lower(): t for t in std.terms.values()}
    out: list[ColumnCheck] = []

    for col in columns:
        c = ColumnCheck(col.table, col.column, col.data_type, col.logical_name, verdict="unknown")
        physical = col.column.lower()

        term = None
        if col.logical_name:
            res = resolve_attribute(std, col.logical_name, type_hint=col.data_type)
            c.notes += res.notes
            if res.status == "common":
                term = std.term(res.logical_name)
                c.matched_by = "logical"
            elif res.status == "local":
                _local_verdict(c, res, physical, col.data_type)
                c.matched_by = "logical"
            elif res.status == "composed":
                c.verdict = "composed"
                c.matched_by = "logical"
                c.std_term, c.std_physical, c.std_domain = res.logical_name, res.physical_name, res.domain
                c.std_type = res.data_type
            else:
                c.verdict = "nonstandard"
        if term is None and c.verdict == "unknown" and physical in by_abbr:
            term = by_abbr[physical]
            c.matched_by = "physical"
            if col.logical_name:  # 한글명은 비표준인데 영문명이 표준 약어와 같음
                c.notes.append(f"영문명은 표준용어 '{term.name}'의 약어와 같음")
        if term is None and c.verdict == "unknown" and not col.logical_name:
            # 한글명이 없는 영문 스키마(BIRD, dvdrental): 영문 이름으로 표준단어를 추정한다
            guess = infer_words(std, col.column)
            if guess:
                words, spans = guess
                res = resolve_attribute(std, "".join(words), type_hint=col.data_type)
                c.matched_by = "english"
                c.notes.append("영문명으로 추정: " + " + ".join(f"{sp}→{w}" for sp, w in zip(spans, words, strict=True)))
                c.notes += res.notes
                if res.status == "common":
                    term = std.term(res.logical_name)
                elif res.status == "local":
                    _local_verdict(c, res, physical, col.data_type)
                elif res.status == "composed":
                    c.verdict = "composed"
                    c.std_term, c.std_physical, c.std_domain = res.logical_name, res.physical_name, res.domain
                    c.std_type = res.data_type

        if term is not None:
            domain = std.domains.get(term.domain)
            c.std_term, c.std_physical, c.std_domain = term.name, term.abbr.lower(), term.domain
            c.std_type = pg_type(domain) if domain else None
            if physical != c.std_physical:
                c.verdict = "name_mismatch"
            elif domain is None:
                c.verdict = "standard"
            else:
                ok, note = _type_ok(col.data_type, domain)
                c.verdict = "standard" if ok else "type_mismatch"
                if note:
                    c.notes.append(note)
            if c.verdict == "name_mismatch" and domain is not None:
                ok, note = _type_ok(col.data_type, domain)
                if not ok:
                    c.notes.append(note)
        out.append(c)
    return CheckReport(standards_version=std.version, columns=out)


def _local_verdict(c: ColumnCheck, res: Any, physical: str, data_type: str) -> None:
    """기관 표준으로 풀린 컬럼: 영문명이 같으면 '기관 표준', 다르면 '영문명 불일치'."""
    c.std_term, c.std_physical, c.std_domain = res.logical_name, res.physical_name, res.domain
    c.std_type = res.data_type
    c.verdict = "local" if physical == res.physical_name else "name_mismatch"


def logical_from_comment(comment: str | None) -> str | None:
    """nlxpg DDL의 COMMENT는 '논리명: 설명' 형식이다. 다른 도구의 COMMENT도 보통 첫
    구절이 한글명이므로 ':' 앞, 없으면 전체를 한글명으로 본다(40자 넘으면 설명으로 보고 버린다)."""
    if not comment:
        return None
    head = comment.split(":", 1)[0].strip()
    return head if head and len(head) <= 40 else None


MAPPING_HEADER = [
    "테이블", "컬럼 한글명", "컬럼 영문명", "데이터타입", "판정",
    "표준용어명", "표준 영문명", "표준도메인", "표준 타입", "비고",
]


def mapping_rows(report: CheckReport) -> list[list[str]]:
    """매뉴얼 [표 Ⅲ-2] 비표준 데이터 매핑정보 형식의 행."""
    return [
        [c.table, c.logical_name or "", c.column, c.data_type, VERDICT_LABEL[c.verdict],
         c.std_term or "", c.std_physical or "", c.std_domain or "", c.std_type or "",
         "; ".join(c.notes)]
        for c in report.columns
    ]
