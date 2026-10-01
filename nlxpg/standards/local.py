"""기관 추가 표준 (ADR-0009).

공통표준 CSV는 고치지 않는다. 기관이 추가한 이음동의어·단어·용어를 시스템 DB(`nlxpg_std_local`)에
두고, CSV로 읽은 공통표준 위에 덧붙인 새 Standards를 만든다. 원본이 우선이라 원본과 겹치는
이름·약어는 받지 않는다.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import re
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from nlxpg.standards.loader import Standards, Term, Word
from nlxpg.standards.resolve import normalize_name, segment

Kind = Literal["alias", "word", "term"]
KIND_LABEL = {"alias": "이음동의어", "word": "단어", "term": "용어"}
_ABBR = re.compile(r"^[A-Z][A-Z0-9]*$")
TERM_ABBR_MAX = 30


@dataclass
class LocalItem:
    kind: Kind
    name: str
    abbr: str = ""            # word: 필수. term: 비우면 단어 약어를 이어 만든다
    english: str = ""
    description: str = ""
    is_format: bool = False   # word
    domain_class: str = ""    # word (형식단어일 때)
    target: str = ""          # alias: 대표단어
    domain: str = ""          # term: 공통표준 도메인명
    item_id: int | None = None

    def snapshot(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.pop("item_id")
        return {k: v for k, v in d.items() if v not in ("", False, None) or k == "kind"}


class LocalConflict(ValueError):
    """원본과 겹치거나 규칙에 맞지 않는 항목."""


def check_item(std: Standards, item: LocalItem, base: Standards) -> LocalItem:
    """항목을 정리·검증한다. std는 지금까지 덧붙인 결과, base는 공통표준 원본이다.
    문제가 있으면 LocalConflict. 정리된 항목(이름 정규화, 용어 약어 생성)을 돌려준다."""
    item = dataclasses.replace(item, name=normalize_name(item.name), abbr=item.abbr.strip().upper(),
                               target=normalize_name(item.target), english=item.english.strip(),
                               description=item.description.strip(), domain=item.domain.strip(),
                               domain_class=item.domain_class.strip())
    if not item.name:
        raise LocalConflict("이름이 비어 있다")
    if item.kind in ("alias", "word") and item.name in std.word_alias:
        src = "공통표준" if item.name in base.word_alias else "기관 표준"
        rep = std.word_alias[item.name]
        raise LocalConflict(f"'{item.name}'은(는) 이미 {src} 단어"
                            + (f"('{rep}'의 이음동의어)" if rep != item.name else "") + "다")

    if item.kind == "alias":
        if item.target not in std.words:
            raise LocalConflict(f"대표단어 '{item.target}'이(가) 단어에 없다")
        return item

    if item.kind == "word":
        if not _ABBR.match(item.abbr):
            raise LocalConflict("영문약어는 영문 대문자로 시작하고 영문 대문자·숫자만 쓴다")
        if item.abbr in {w.abbr for w in std.words.values()}:
            owner = next(w.name for w in std.words.values() if w.abbr == item.abbr)
            raise LocalConflict(f"영문약어 '{item.abbr}'은(는) 이미 단어 '{owner}'이(가) 쓴다")
        if item.is_format:
            if item.domain_class not in {d.klass for d in std.domains.values()}:
                raise LocalConflict(f"형식단어는 공통표준 도메인 분류가 필요하다: '{item.domain_class}'")
        else:
            item.domain_class = ""
        return item

    # term
    if std.term(item.name) is not None:
        src = "공통표준" if base.term(item.name) is not None else "기관 표준"
        raise LocalConflict(f"'{item.name}'은(는) 이미 {src} 용어다")
    pieces = segment(std, item.name)
    if pieces is None:
        raise LocalConflict(f"'{item.name}'을(를) 단어로 나눌 수 없다 — 없는 단어를 먼저 기관 단어로 추가한다")
    words = [std.word_alias[p] for p in pieces]
    auto = "_".join(std.words[w].abbr for w in words)
    item.abbr = item.abbr or auto
    if item.abbr != auto:
        raise LocalConflict(f"용어 영문약어는 단어 약어를 이은 '{auto}'여야 한다")
    if len(item.abbr) > TERM_ABBR_MAX:
        raise LocalConflict(f"영문약어 {len(item.abbr)}자 (상한 {TERM_ABBR_MAX}자)")
    if item.abbr in {t.abbr for t in std.terms.values()}:
        owner = next(t.name for t in std.terms.values() if t.abbr == item.abbr)
        raise LocalConflict(f"영문약어 '{item.abbr}'은(는) 이미 용어 '{owner}'이(가) 쓴다")
    if item.domain not in std.domains:
        raise LocalConflict(f"공통표준 도메인이 아니다: '{item.domain}'")
    if not item.english:
        item.english = " ".join(std.words[w].english for w in words)
    return item


@dataclass
class Applied:
    standards: Standards
    applied: list[LocalItem]
    #: 적용하지 못한 항목과 이유 (공통표준 새 판과 충돌 등)
    rejected: list[tuple[LocalItem, str]]


def apply_local(base: Standards, items: list[LocalItem]) -> Applied:
    """공통표준 원본 위에 기관 항목을 덧붙인 새 Standards. base는 바꾸지 않는다.
    단어 → 이음동의어 → 용어 순으로 적용한다(이음동의어·용어가 기관 단어를 쓸 수 있게)."""
    std = dataclasses.replace(
        base, words=dict(base.words), word_alias=dict(base.word_alias), forbidden=set(base.forbidden),
        terms=dict(base.terms), term_alias=dict(base.term_alias),
        domain_term_count=dict(base.domain_term_count), local_aliases=set(),
    )
    applied: list[LocalItem] = []
    rejected: list[tuple[LocalItem, str]] = []
    order = {"word": 0, "alias": 1, "term": 2}
    for it in sorted(items, key=lambda i: (order[i.kind], i.item_id or 0)):
        try:
            it2 = check_item(std, it, base)
        except LocalConflict as exc:
            rejected.append((it, str(exc)))
            continue
        it2.item_id = it.item_id
        if it2.kind == "word":
            std.words[it2.name] = Word(it2.name, it2.abbr, it2.english, it2.description,
                                       it2.is_format, it2.domain_class, local=True)
            std.word_alias[it2.name] = it2.name
        elif it2.kind == "alias":
            std.word_alias[it2.name] = it2.target
            std.local_aliases.add(it2.name)
        else:
            std.terms[it2.name] = Term(it2.name, it2.description, it2.abbr, it2.domain, "", "", local=True)
            std.domain_term_count[it2.domain] = std.domain_term_count.get(it2.domain, 0) + 1
        applied.append(it2)
    std.local_items = len(applied)
    std.local_snapshot = [i.snapshot() for i in applied]
    return Applied(std, applied, rejected)


# ── 시스템 DB ────────────────────────────────────────────

_COLS = ("kind", "name", "abbr", "english", "description", "is_format", "domain_class", "target", "domain")


class LocalStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list(self) -> list[LocalItem]:
        rows = await self._pool.fetch(f"SELECT item_id, {', '.join(_COLS)} FROM nlxpg_std_local ORDER BY item_id")
        return [LocalItem(**dict(r)) for r in rows]

    async def rows(self) -> list[dict[str, Any]]:
        """화면용: 누가 언제 만들었는지까지."""
        rows = await self._pool.fetch(f"""
            SELECT l.item_id, {', '.join('l.' + c for c in _COLS)}, l.created_at, l.updated_at,
                   u.display_name AS created_by_name
            FROM nlxpg_std_local l LEFT JOIN nlxpg_users u ON u.user_id = l.created_by ORDER BY l.item_id""")
        return [dict(r) for r in rows]

    async def add(self, item: LocalItem, user_id: int | None) -> int:
        return await self._pool.fetchval(
            f"INSERT INTO nlxpg_std_local ({', '.join(_COLS)}, created_by) "
            f"VALUES ({', '.join(f'${i + 1}' for i in range(len(_COLS)))}, ${len(_COLS) + 1}) RETURNING item_id",
            *(getattr(item, c) for c in _COLS), user_id)

    async def update(self, item_id: int, item: LocalItem) -> bool:
        sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(_COLS))
        r = await self._pool.execute(
            f"UPDATE nlxpg_std_local SET {sets}, updated_at = now() WHERE item_id = $1",
            item_id, *(getattr(item, c) for c in _COLS))
        return r.endswith("1")

    async def delete(self, item_id: int) -> bool:
        return (await self._pool.execute("DELETE FROM nlxpg_std_local WHERE item_id = $1", item_id)).endswith("1")


async def import_items(
    store: LocalStore, base: Standards, items: list[LocalItem], user_id: int | None,
) -> tuple[int, int, list[str]]:
    """CSV 등에서 읽은 항목을 한 행씩 검증해 넣는다(웹 API·CLI 공용).
    이미 같은 (구분, 이름)이 있으면 건너뛰고, 문제가 있는 행은 이유를 모은다. → (추가, 건너뜀, 오류)"""
    existing = await store.list()
    have = {(i.kind, normalize_name(i.name)) for i in existing}
    std = apply_local(base, existing).standards
    added, skipped, errors = 0, 0, []
    order = {"word": 0, "alias": 1, "term": 2}  # 단어를 먼저 넣어야 이음동의어·용어가 그 단어를 쓴다
    for item in sorted(items, key=lambda i: order[i.kind]):
        if (item.kind, normalize_name(item.name)) in have:
            skipped += 1
            continue
        try:
            checked = check_item(std, item, base)
        except LocalConflict as exc:
            errors.append(f"{KIND_LABEL[item.kind]} '{item.name}': {exc}")
            continue
        checked.item_id = await store.add(checked, user_id)
        existing.append(checked)
        have.add((checked.kind, checked.name))
        std = apply_local(base, existing).standards
        added += 1
    return added, skipped, errors


def dependents(items: list[LocalItem], std: Standards, word: str) -> list[str]:
    """기관 단어 word를 쓰는 기관 이음동의어·용어 (지우기 전에 확인)."""
    out = [f"이음동의어 '{i.name}'" for i in items if i.kind == "alias" and i.target == word]
    for i in items:
        if i.kind == "term":
            pieces = segment(std, i.name) or []
            if word in (std.word_alias.get(p) for p in pieces):
                out.append(f"용어 '{i.name}'")
    return out


# ── CSV (한 파일, 구분 열로 종류를 나눈다) ─────────────────

CSV_HEADER = ["구분", "이름", "영문약어명", "영문명", "설명", "형식단어여부", "도메인분류명", "대표단어", "도메인명"]
_LABEL_KIND = {v: k for k, v in KIND_LABEL.items()}


def to_csv(items: list[LocalItem]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for i in items:
        w.writerow([KIND_LABEL[i.kind], i.name, i.abbr, i.english, i.description,
                    "Y" if i.is_format else ("N" if i.kind == "word" else ""), i.domain_class, i.target, i.domain])
    return "﻿" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않게 BOM


def from_csv(text: str) -> list[LocalItem]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    out = []
    for n, r in enumerate(rows, start=2):
        kind = _LABEL_KIND.get((r.get("구분") or "").strip())
        if kind is None:
            raise LocalConflict(f"{n}행: 구분은 이음동의어·단어·용어 중 하나다")
        out.append(LocalItem(
            kind=kind, name=r.get("이름", ""), abbr=r.get("영문약어명", ""), english=r.get("영문명", ""),
            description=r.get("설명", ""), is_format=(r.get("형식단어여부", "").strip().upper() == "Y"),
            domain_class=r.get("도메인분류명", ""), target=r.get("대표단어", ""), domain=r.get("도메인명", ""),
        ))
    return out
