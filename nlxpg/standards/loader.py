"""공통표준 CSV 로더.

data/standards/의 `{공통표준용어|공통표준단어|공통표준도메인}_{YYYYMMDD}.csv`를 읽는다.
판(version)을 지정하지 않으면 세 파일이 모두 있는 가장 최근 판을 쓴다.

폐기된 항목도 행으로 남아 있어 여기서 걸러낸다. 폐기 표시는 두 군데에 흩어져 있다
(data/standards/README.md): `개정구분명`이 '폐기'이거나 `제정차수`에 '(폐기)'가 있으면
폐기다. '(폐기후제정)'은 새로 제정된 유효 항목이다.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("공통표준용어", "공통표준단어", "공통표준도메인")
_RETIRED = re.compile(r"\(\s*폐기\s*\)")
_REVISION_COL = "개정구분명(폐기 또는 변경)"


@dataclass(frozen=True)
class Word:
    name: str
    abbr: str
    english: str
    description: str
    is_format: bool
    domain_class: str  # 형식단어일 때만


@dataclass(frozen=True)
class Domain:
    group: str
    klass: str
    name: str
    description: str
    data_type: str  # CHAR | VARCHAR | NUMERIC | DATETIME
    length: int | None
    scale: int | None
    allowed: str
    storage_format: str = ""
    display_format: str = ""
    unit: str = ""


@dataclass(frozen=True)
class Term:
    name: str
    description: str
    abbr: str
    domain: str
    allowed: str
    storage_format: str


@dataclass
class Standards:
    version: str
    words: dict[str, Word] = field(default_factory=dict)
    #: 이음동의어·금칙어 → 대표단어명. 대표단어 자신도 포함한다.
    word_alias: dict[str, str] = field(default_factory=dict)
    forbidden: set[str] = field(default_factory=set)
    terms: dict[str, Term] = field(default_factory=dict)
    #: 용어 이음동의어 → 대표 용어명
    term_alias: dict[str, str] = field(default_factory=dict)
    domains: dict[str, Domain] = field(default_factory=dict)
    #: 도메인분류명 → 그 분류를 쓰는 용어가 가장 많은 도메인명 (길이를 모를 때의 기본값)
    default_domain: dict[str, str] = field(default_factory=dict)
    #: 도메인명 → 그 도메인을 쓰는 (유효) 표준용어 수
    domain_term_count: dict[str, int] = field(default_factory=dict)

    def domains_in_class(self, klass: str) -> list[Domain]:
        return [d for d in self.domains.values() if d.klass == klass]

    def term(self, name: str) -> Term | None:
        return self.terms.get(self.term_alias.get(name, name))


def _is_retired(row: dict[str, str]) -> bool:
    revision = next((v for k, v in row.items() if k.startswith("개정구분")), "")
    return revision.strip() == "폐기" or bool(_RETIRED.search(row.get("제정차수", "")))


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[,，]", value or "") if v.strip()]


def _int(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if not _is_retired(r)]


def available_versions(directory: Path) -> list[str]:
    found: dict[str, set[str]] = {}
    for p in directory.glob("*_*.csv"):
        kind, _, ver = p.stem.rpartition("_")
        if kind in KINDS and ver.isdigit():
            found.setdefault(ver, set()).add(kind)
    return sorted(v for v, kinds in found.items() if kinds == set(KINDS))


def load_standards(directory: str | Path, version: str | None = None) -> Standards:
    directory = Path(directory)
    versions = available_versions(directory)
    if not versions:
        raise FileNotFoundError(f"공통표준 CSV 3종이 없다: {directory}")
    if version is None:
        version = versions[-1]
    elif version not in versions:
        raise FileNotFoundError(f"공통표준 {version}판이 없다 (있는 판: {', '.join(versions)})")

    def path(kind: str) -> Path:
        return directory / f"{kind}_{version}.csv"

    std = Standards(version=version)

    for r in _read(path("공통표준도메인")):
        d = Domain(
            group=r["공통표준도메인그룹명"].strip(), klass=r["공통표준도메인분류명"].strip(),
            name=r["공통표준도메인명"].strip(), description=r["공통표준도메인설명"].strip(),
            data_type=r["데이터타입"].strip().upper(), length=_int(r["데이터길이"]),
            scale=_int(r["데이터소수점길이"]), allowed=r["허용값"].strip(),
            storage_format=r["저장형식"].strip(), display_format=r["표현형식"].strip(),
            unit=r["단위"].strip(),
        )
        std.domains[d.name] = d

    for r in _read(path("공통표준단어")):
        w = Word(
            name=r["공통표준단어명"].strip(), abbr=r["공통표준단어영문약어명"].strip().upper(),
            english=r["공통표준단어 영문명"].strip(), description=r["공통표준단어 설명"].strip(),
            is_format=r["형식단어여부"].strip().upper() == "Y",
            domain_class=r["공통표준도메인분류명"].strip(),
        )
        std.words[w.name] = w
        std.word_alias[w.name] = w.name
    # 대표단어를 먼저 모두 등록한 뒤 별칭을 붙인다. 어떤 단어의 이음동의어가 다른
    # 대표단어와 같은 경우(두 표준단어가 서로를 동의어로 적은 경우) 대표단어가 이긴다.
    for r in _read(path("공통표준단어")):
        name = r["공통표준단어명"].strip()
        for alias in _split_list(r["이음동의어 목록"]):
            std.word_alias.setdefault(alias, name)
        for bad in _split_list(r["금칙어 목록"]):
            std.word_alias.setdefault(bad, name)
            std.forbidden.add(bad)

    usage: dict[str, Counter[str]] = {}
    for r in _read(path("공통표준용어")):
        t = Term(
            name=r["공통표준용어명"].strip(), description=r["공통표준용어설명"].strip(),
            abbr=r["공통표준용어영문약어명"].strip().upper(), domain=r["공통표준도메인명"].strip(),
            allowed=r["허용값"].strip(), storage_format=r["저장 형식"].strip(),
        )
        std.terms[t.name] = t
        for alias in _split_list(r["용어 이음동의어 목록"]):
            std.term_alias.setdefault(alias, t.name)
        dom = std.domains.get(t.domain)
        if dom:
            usage.setdefault(dom.klass, Counter())[dom.name] += 1
            std.domain_term_count[dom.name] = std.domain_term_count.get(dom.name, 0) + 1

    for klass in {d.klass for d in std.domains.values()}:
        counted = usage.get(klass)
        std.default_domain[klass] = (
            counted.most_common(1)[0][0] if counted else std.domains_in_class(klass)[0].name
        )
    return std
