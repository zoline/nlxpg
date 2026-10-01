"""설계된 스키마의 정규형 검사 (데이터 없이).

pgxnl Design Lab(pgxnl/agent/designlab)은 운영 DB의 **데이터**로 함수 종속을 확인한다.
새 설계에는 데이터가 없으므로 이 모듈은 다음 단서만으로 판단한다. 전부 결정론적이다.

- 이름: 논리명을 공통표준 단어로 분해한다(`상품코드` = 상품 + 코드). 표준이 없으면 글자 접두사로 대신한다.
- 관계: FK 그래프 (이 속성의 주인 엔터티로 가는 FK가 이미 있는가)
- 값 예시: 문서에서 뽑은 예시 값

판정 수준을 둘로 나눈다. 이름만으로는 "사본"과 "주문 당시 값을 따로 보관" 같은 의도적
중복을 구분할 수 없기 때문이다(주문품목의 단가 vs 상품의 정가).

- violation: 구조상 위반이 분명함 (예: FK로 이미 닿는 엔터티의 속성을 사본으로 가짐)
- review: 사람이 확인해야 함 (예: 코드-이름 쌍, 파생 속성)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from nlxpg.ir import Attribute, Entity, SchemaIR
from nlxpg.standards import Standards
from nlxpg.standards.resolve import normalize_name, segment

Level = Literal["violation", "review"]


@dataclass
class NFFinding:
    kind: str          # repeating_group | multivalued | partial_dependency | foreign_copy | missing_fk | code_name_pair | derived
    normal_form: str   # 1NF | 2NF | 3NF | 파생 | 관계
    level: Level
    table: str
    columns: list[str]
    title: str
    detail: str
    suggestion: str


@dataclass
class _Ctx:
    ir: SchemaIR
    std: Standards | None
    fk_cols: dict[str, set[str]] = field(default_factory=dict)       # 테이블 → FK 컬럼
    fk_targets: dict[str, dict[str, str]] = field(default_factory=dict)  # 테이블 → {FK 컬럼: 부모}
    children: dict[str, set[str]] = field(default_factory=dict)       # 부모 → 1:N 자식

    def words(self, name: str) -> list[str]:
        name = normalize_name(name)
        if self.std is not None:
            pieces = segment(self.std, name)
            if pieces:
                return [self.std.word_alias[p] for p in pieces]
        return [name]


def check_normal_forms(ir: SchemaIR, standards: Standards | None = None) -> list[NFFinding]:
    ctx = _Ctx(ir, standards)
    for r in ir.relationships:
        if r.foreign_key:
            t = r.foreign_key.table
            ctx.fk_cols.setdefault(t, set()).update(r.foreign_key.columns)
            for c in r.foreign_key.columns:
                ctx.fk_targets.setdefault(t, {})[c] = r.from_entity
            ctx.children.setdefault(r.from_entity, set()).add(t)

    out: list[NFFinding] = []
    for e in ir.entities:
        out += _repeating_groups(e)
        out += _multivalued(e, ctx)
        out += _partial_dependency(e, ctx)
        out += _foreign_copies(e, ctx)
        out += _code_name_pairs(e, ctx)
        out += _derived(e, ctx)
    order = {"violation": 0, "review": 1}
    return sorted(out, key=lambda f: (order[f.level], f.normal_form, f.table))


def _data_attrs(e: Entity, ctx: _Ctx) -> list[Attribute]:
    """키와 FK를 뺀 일반 속성."""
    fks = ctx.fk_cols.get(e.physical_name, set())
    return [a for a in e.attributes if not a.is_primary_key and a.physical_name not in fks]


# ── 1NF ────────────────────────────────────────────────

_TRAILING_NUM = re.compile(r"^(?P<base>.*?\D)_?(?P<n>\d{1,2})$")


def _repeating_groups(e: Entity) -> list[NFFinding]:
    """`전화번호1`, `전화번호2`처럼 번호만 다른 속성 묶음 (pgxnl detect.find_repeating_groups와 같은 규칙).
    번호가 의미의 일부인 경우(12학년)를 오인하지 않도록 2개 이상일 때만 본다."""
    groups: dict[str, list[str]] = {}
    for a in e.attributes:
        m = _TRAILING_NUM.match(normalize_name(a.logical_name)) or _TRAILING_NUM.match(a.physical_name)
        if m:
            groups.setdefault(m.group("base").lower(), []).append(a.physical_name)
    return [
        NFFinding(
            "repeating_group", "1NF", "violation", e.physical_name, sorted(cols),
            f"반복 그룹: {base}",
            f"같은 속성이 번호를 달고 {len(cols)}개 컬럼으로 펼쳐져 있다: {', '.join(sorted(cols))}",
            "자식 엔터티로 분리해 여러 행으로 저장한다. 번호가 의미를 가지면 "
            "구분 수식어로 이름을 바꾼다(매뉴얼 105쪽: 회사전화번호, 휴대전화번호).",
        )
        for base, cols in groups.items() if len(cols) >= 2
    ]


_LIST_SEP = re.compile(r"\s*[,;/·|]\s*")
_NUMBER = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")


def _multivalued(e: Entity, ctx: _Ctx) -> list[NFFinding]:
    out = []
    for a in _data_attrs(e, ctx):
        listy = [v for v in a.value_examples
                 if not _NUMBER.match(v.strip()) and len([p for p in _LIST_SEP.split(v) if p]) >= 2]
        if listy:
            out.append(NFFinding(
                "multivalued", "1NF", "review", e.physical_name, [a.physical_name],
                f"다중값 의심: {a.logical_name}",
                f"값 예시가 여러 값을 한 칸에 담은 것처럼 보인다: {listy[0]!r}",
                "값마다 한 행이 되도록 자식 엔터티(또는 연결 테이블)로 분리한다. 한 값이 원래 구분자를 "
                "포함하는 것이면(주소 등) 무시한다.",
            ))
    return out


# ── 2NF ────────────────────────────────────────────────


def _starts_with(words: list[str], prefix: list[str]) -> bool:
    if len(prefix) == 1 and len(words) == 1:  # 표준 분할이 안 된 경우: 글자 접두사
        return words[0] != prefix[0] and words[0].startswith(prefix[0])
    return len(words) > len(prefix) and words[: len(prefix)] == prefix


def _partial_dependency(e: Entity, ctx: _Ctx) -> list[NFFinding]:
    """복합 PK의 일부(= 부모 엔터티 하나)에만 딸린 속성. 연결 테이블에 '상품명'이 있는 경우 등."""
    if len(e.primary_key) < 2:
        return []
    targets = ctx.fk_targets.get(e.physical_name, {})
    parents = {targets[c] for c in e.primary_key if c in targets}
    out = []
    for a in _data_attrs(e, ctx):
        aw = ctx.words(a.logical_name)
        for p in parents:
            pe = ctx.ir.entity(p)
            if pe and _starts_with(aw, ctx.words(pe.logical_name)):
                out.append(NFFinding(
                    "partial_dependency", "2NF", "violation", e.physical_name, [a.physical_name],
                    f"부분 종속: {a.logical_name}",
                    f"PK({' + '.join(e.primary_key)}) 중 '{pe.logical_name}' 쪽에만 딸린 속성이다.",
                    f"{pe.physical_name} 테이블로 옮긴다.",
                ))
                break
    return out


# ── 3NF ────────────────────────────────────────────────


def _owner(attr_words: list[str], ctx: _Ctx) -> Entity | None:
    """속성 이름이 어느 엔터티 이름으로 시작하는지. 여럿이면 가장 긴 이름(주문품목 > 주문)."""
    matches = [(len(ctx.words(e.logical_name)), len(e.logical_name), e) for e in ctx.ir.entities
               if _starts_with(attr_words, ctx.words(e.logical_name))]
    return max(matches, key=lambda m: (m[0], m[1]))[2] if matches else None


def _reaches(ctx: _Ctx, src: str, dst: str) -> bool:
    """src 테이블에서 FK를 따라 dst에 닿는가 (다단계 포함)."""
    seen, stack = {src}, [src]
    while stack:
        t = stack.pop()
        for parent in ctx.fk_targets.get(t, {}).values():
            if parent == dst:
                return True
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return False


#: "엔터티명 + 이 단어"면 그 엔터티의 행 수를 센 값이다 (게시글.좋아요수 = 좋아요 행 수).
_COUNT_WORDS = ("수", "건수", "개수", "횟수")


def _rest(attr_words: list[str], owner_words: list[str]) -> list[str]:
    """속성 이름에서 주인 엔터티 이름을 뗀 나머지. 표준 분할이 안 된 경우는 글자로 뗀다."""
    if len(owner_words) == 1 and len(attr_words) == 1:
        return [attr_words[0][len(owner_words[0]):]]
    return attr_words[len(owner_words):]


def _missing_fk(e: Entity, a: Attribute, ctx: _Ctx) -> NFFinding | None:
    """다른 엔터티의 PK와 이름이 같은데 FK가 없는 속성 (북마크.회원아이디)."""
    for other in ctx.ir.entities:
        if other is e or len(other.primary_key) != 1:
            continue
        pk = other.attribute(other.primary_key[0])
        if pk and (pk.physical_name == a.physical_name or pk.logical_name == a.logical_name):
            return NFFinding(
                "missing_fk", "관계", "review", e.physical_name, [a.physical_name],
                f"관계 누락 의심: {a.logical_name}",
                f"'{other.logical_name}'({other.physical_name})의 PK와 이름이 같은데 FK가 없다. "
                "참조 무결성이 보장되지 않는다.",
                f"FK({e.physical_name}.{a.physical_name} → {other.physical_name}.{pk.physical_name})를 추가한다.",
            )
    return None


def _foreign_copies(e: Entity, ctx: _Ctx) -> list[NFFinding]:
    """다른 엔터티의 속성을 사본으로 가진 경우 (주문품목.상품코드). FK로 이미 닿으면 이행 종속이다.

    **다른 엔터티 이름으로 시작하는 속성만 본다.** '내용', '작성일시'처럼 여러 테이블이 각자
    갖는 이름은 같아도 사본이 아니다 — pgxnl Design Lab도 같은 문제로 GENERIC_COLUMNS를 두었다.
    실측(2026-10-01, 게시판 작성규정 run 6): 이름만 같으면 사본으로 보는 규칙이 13건을 오탐했다.
    """
    out = []
    for a in _data_attrs(e, ctx):
        if missing := _missing_fk(e, a, ctx):
            out.append(missing)
            continue
        aw = ctx.words(a.logical_name)
        owner = _owner(aw, ctx)
        # 자기 엔터티 이름으로도 시작하면(주문품목의 '주문품목수량') 자기 속성이다
        if owner is None or owner is e or _starts_with(aw, ctx.words(e.logical_name)):
            continue
        rest = _rest(aw, ctx.words(owner.logical_name))
        if len(rest) == 1 and rest[0] in _COUNT_WORDS:
            out.append(NFFinding(
                "derived", "파생", "review", e.physical_name, [a.physical_name],
                f"파생 속성 의심: {a.logical_name}",
                f"'{owner.logical_name}'({owner.physical_name}) 행 수를 센 값을 따로 저장한다. "
                "행이 추가·삭제될 때 함께 고치지 않으면 값이 어긋난다.",
                "COUNT로 계산해서 쓴다. 조회 성능 때문에 저장한다면 속성유형을 '추출형'으로 표시하고 "
                "갱신 규칙(트리거 등)을 속성 설명에 적는다.",
            ))
            continue
        same = next((b for b in owner.attributes
                     if b.physical_name == a.physical_name or b.logical_name == a.logical_name), None)
        note = f" (같은 이름: {owner.physical_name}.{same.physical_name})" if same else ""
        if _reaches(ctx, e.physical_name, owner.physical_name):
            level: Level = "violation"
            detail = (f"FK로 이미 '{owner.logical_name}'({owner.physical_name})에 닿는다. "
                      f"'{a.logical_name}' 값은 {e.physical_name} PK → FK → {owner.physical_name} 경로로 "
                      "정해지는 이행 종속이다.")
            fix = ("컬럼을 지우고 조인으로 읽는다. 특정 시점의 값을 일부러 보관하는 것이면(예: 주문 당시 가격) "
                   "'당시' 같은 수식어를 붙여 이름으로 의도를 드러낸다.")
        else:
            level = "review"
            detail = (f"'{owner.logical_name}' 엔터티의 속성으로 보이지만, "
                      f"{e.physical_name} → {owner.physical_name} FK가 없다.")
            fix = f"관계가 빠졌는지 확인한다. 관계가 맞다면 FK를 두고 이 값은 {owner.physical_name}에서 읽는다."
        out.append(NFFinding(
            "foreign_copy", "3NF", level, e.physical_name, [a.physical_name],
            f"다른 엔터티 속성의 사본: {a.logical_name}{note}", detail, fix,
        ))
    return out


_CODE_SUFFIX = ("코드", "번호")
_NAME_SUFFIX = ("명", "이름", "명칭")


def _code_name_pairs(e: Entity, ctx: _Ctx) -> list[NFFinding]:
    """한 테이블 안의 `X코드` / `X명` 쌍. X가 이 엔터티 자신이면(상품의 상품코드·상품명) 정상이다."""
    by_name = {normalize_name(a.logical_name): a for a in _data_attrs(e, ctx)}
    own = normalize_name(e.logical_name)
    out = []
    for name, a in by_name.items():
        for cs in _CODE_SUFFIX:
            if not name.endswith(cs) or len(name) == len(cs):
                continue
            stem = name[: -len(cs)]
            if stem == own:
                continue
            for ns in _NAME_SUFFIX:
                b = by_name.get(stem + ns)
                if b:
                    out.append(NFFinding(
                        "code_name_pair", "3NF", "review", e.physical_name,
                        [a.physical_name, b.physical_name],
                        f"코드-이름 쌍: {a.logical_name} / {b.logical_name}",
                        f"'{a.logical_name}' 값 하나에 '{b.logical_name}' 값이 하나로 정해지면"
                        "(같은 코드 → 항상 같은 이름) 이행 종속이다.",
                        f"'{stem}' 참조(코드) 테이블을 두고 {a.physical_name}만 FK로 남긴다. "
                        "행정표준코드가 있으면 그것을 쓴다(매뉴얼 149쪽).",
                    ))
    return out


# ── 파생 속성 ──────────────────────────────────────────

_DERIVED = re.compile(r"(총|합계|누계|평균|소계)")
_NUMERIC = re.compile(r"^(numeric|integer|bigint|smallint|real|double)")


def _derived(e: Entity, ctx: _Ctx) -> list[NFFinding]:
    """`주문총금액`처럼 자식 행에서 계산할 수 있는 값을 저장하는 속성 (매뉴얼의 추출형 속성)."""
    kids = [ctx.ir.entity(c) for c in ctx.children.get(e.physical_name, set())]
    sources = [
        (k, [b for b in _data_attrs(k, ctx) if _NUMERIC.match(b.data_type)]) for k in kids if k
    ]
    sources = [(k, cols) for k, cols in sources if cols]
    if not sources:
        return []
    out = []
    for a in _data_attrs(e, ctx):
        if _DERIVED.search(a.logical_name) and _NUMERIC.match(a.data_type):
            k, cols = sources[0]
            out.append(NFFinding(
                "derived", "파생", "review", e.physical_name, [a.physical_name],
                f"파생 속성 의심: {a.logical_name}",
                f"자식 테이블 {k.physical_name}의 컬럼({', '.join(b.logical_name for b in cols)})에서 "
                "계산할 수 있는 값을 따로 저장한다. 원본이 바뀌면 값이 어긋날 수 있다.",
                "계산해서 쓴다(뷰). 성능 때문에 저장한다면 속성유형을 '추출형'으로 표시하고 갱신 규칙을 "
                "속성 설명에 적는다(지침 별표 제2호).",
            ))
    return out
