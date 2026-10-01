"""논리명(한글) → 표준 물리명·도메인·PostgreSQL 타입 (ADR-0005).

순서:
1. 공통표준용어에 있으면(용어 이음동의어 포함) 그 영문약어명과 도메인 → status "common"
2. 표준단어로 분할되면 대표단어로 바꾼 뒤 다시 1을 시도하고, 없으면 단어 영문약어를
   `_`로 이어 새 용어를 만든다 → status "composed" (DB표준 후보)
3. 분할되지 않으면 → status "nonstandard" (호출자가 원래 값을 유지)

물리명은 소문자로 쓴다. 날짜·일시 도메인은 PostgreSQL 네이티브 타입으로 바꾼다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from nlxpg.standards.loader import Domain, Standards

Status = Literal["common", "composed", "nonstandard"]

#: ADR-0005: 날짜·일시 도메인분류 → PostgreSQL 타입. 나머지 날짜류(연도, 연월 등)는 char 유지.
_DATE_CLASSES = {"연월일": "date", "연월일시분초": "timestamp", "연월일시분": "timestamp"}
TERM_ABBR_MAX = 30  # 매뉴얼 108쪽

_CLEAN = re.compile(r"[\s_\-()/+·.,]")
_HINT = re.compile(r"^\s*(\w+(?:\s\w+)?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$")


@dataclass
class Resolution:
    status: Status
    logical_name: str  # 표준 적용 후 논리명 (대표단어로 치환된 이름)
    physical_name: str | None = None
    domain: str | None = None
    data_type: str | None = None
    words: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def pg_type(domain: Domain, *, adapt_dates: bool = True) -> str:
    """adapt_dates=False면 공통표준 원형 타입(연월일C8 → char(8))을 돌려준다."""
    if adapt_dates and domain.klass in _DATE_CLASSES:
        return _DATE_CLASSES[domain.klass]
    if domain.data_type == "DATETIME":
        return "timestamp"
    if domain.data_type == "CHAR":
        return f"char({domain.length or 1})"
    if domain.data_type == "VARCHAR":
        return f"varchar({domain.length})" if domain.length else "text"
    if domain.data_type == "NUMERIC":
        if domain.length and domain.scale:
            return f"numeric({domain.length},{domain.scale})"
        return f"numeric({domain.length})" if domain.length else "numeric"
    return "text"


def normalize_name(name: str) -> str:
    """용어명 규칙상 공백·특수문자는 쓰지 않고, 영문은 대문자다(매뉴얼 105·116쪽)."""
    return _CLEAN.sub("", name).upper()


def segment(std: Standards, name: str) -> list[str] | None:
    """표준단어(이음동의어·금칙어 포함)로 빠짐없이 분할한다. 분할 수가 가장 적은 것을
    고른다 — 복합어(전화번호)가 단어 조합(전화+번호)보다 우선이다(매뉴얼 105쪽)."""
    n = len(name)
    maxlen = max((len(k) for k in std.word_alias), default=0)
    best: list[list[str] | None] = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 1, min(n, i + maxlen) + 1):
            piece = name[i:j]
            if piece in std.word_alias:
                cand = best[i] + [piece]  # type: ignore[operator]
                if best[j] is None or len(cand) < len(best[j]):  # type: ignore[arg-type]
                    best[j] = cand
    return best[n]


def _pick_domain(std: Standards, klass: str, hint: str, examples: list[str]) -> Domain | None:
    """형식단어의 도메인분류 안에서 길이를 고른다. 타입 힌트의 길이와 값 예시의 길이
    (한글 2바이트, 공통표준 길이 기준) 중 큰 값 이상인 가장 짧은 도메인. 둘 다 없으면
    그 분류에서 용어가 가장 많이 쓰는 도메인."""
    candidates = std.domains_in_class(klass)
    if not candidates:
        return None
    m = _HINT.match(hint.lower()) if hint else None
    want = max(
        int(m.group(2)) if m and m.group(2) else 0,
        max((_byte_len(v) for v in examples), default=0),
    )
    if want:
        fitting = sorted((d for d in candidates if (d.length or 0) >= want), key=lambda d: d.length or 0)
        if fitting:
            return fitting[0]
    return std.domains.get(std.default_domain.get(klass, ""), candidates[0])


def _byte_len(value: str) -> int:
    return sum(2 if ord(ch) > 0x7F else 1 for ch in value.strip())


def _from_term(std: Standards, term_name: str, res: Resolution) -> Resolution | None:
    term = std.term(term_name)
    if term is None:
        return None
    domain = std.domains.get(term.domain)
    res.status = "common"
    res.logical_name = term.name
    res.physical_name = term.abbr.lower()
    res.domain = term.domain
    res.data_type = pg_type(domain) if domain else None
    return res


def resolve_attribute(
    std: Standards, logical_name: str, *, type_hint: str = "", examples: list[str] | None = None
) -> Resolution:
    name = normalize_name(logical_name)
    res = Resolution(status="nonstandard", logical_name=name)
    if not name:
        return res
    if hit := _from_term(std, name, res):
        return hit

    pieces = segment(std, name)
    if pieces is None:
        res.notes.append("표준단어로 분할되지 않음")
        return res

    words = [std.word_alias[p] for p in pieces]
    for p, w in zip(pieces, words, strict=True):
        if p in std.forbidden:
            res.notes.append(f"금칙어 '{p}' → '{w}'")
        elif p != w:
            res.notes.append(f"이음동의어 '{p}' → '{w}'")
    res.words = words
    representative = "".join(words)
    if representative != name and (hit := _from_term(std, representative, res)):
        return hit

    res.status = "composed"
    res.logical_name = representative
    res.physical_name = "_".join(std.words[w].abbr for w in words).lower()
    if len(res.physical_name) > TERM_ABBR_MAX:
        res.notes.append(f"영문약어명 {len(res.physical_name)}자 (상한 {TERM_ABBR_MAX}자)")

    last = std.words[words[-1]]
    if last.is_format and last.domain_class:
        domain = _pick_domain(std, last.domain_class, type_hint, examples or [])
        if domain:
            res.domain = domain.name
            res.data_type = pg_type(domain)
    else:
        res.notes.append(f"마지막 단어 '{last.name}'이(가) 형식단어가 아님")
    return res


def resolve_entity(std: Standards, logical_name: str) -> Resolution:
    """테이블명. 공통표준에는 테이블 명명규칙이 없어(기관이 정함) 단어 조합만 쓴다.
    형식단어로 끝날 필요는 없다."""
    name = normalize_name(logical_name)
    res = Resolution(status="nonstandard", logical_name=name)
    pieces = segment(std, name) if name else None
    if pieces is None:
        res.notes.append("표준단어로 분할되지 않음")
        return res
    words = [std.word_alias[p] for p in pieces]
    res.status = "composed"
    res.words = words
    res.logical_name = "".join(words)
    res.physical_name = "_".join(std.words[w].abbr for w in words).lower()
    return res
