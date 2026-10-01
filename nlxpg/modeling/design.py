"""정합된 IR → 설계된 IR.

결정론적 규칙만 쓴다(베이스라인). 적용 순서:
0. 공통표준 적용 (standards가 주어지면, ADR-0005): 물리명·도메인·타입
1. 물리명 정규화·중복 제거
2. 타입 정규화 (힌트가 없으면 값 예시로 추론)
3. 관계 정리: 없는 엔티티를 가리키는 관계 제거, N:1은 1:N으로 뒤집기
4. PK: 명시된 PK가 없으면 {table}_id bigint 대리키
5. FK: 1:N은 자식에, 1:1은 to 쪽에 UNIQUE FK, N:M은 연결 테이블

3NF 정규화 검사는 아직 없다(3단계 파이프라인 고도화 범위).
"""
from __future__ import annotations

import logging

from nlxpg.ir import Attribute, Entity, ForeignKey, Relationship, SchemaIR
from nlxpg.modeling.naming import surrogate_key_name, to_identifier, unique_name
from nlxpg.modeling.types import infer_from_examples, normalize_type
from nlxpg.reconcile.merge import merge_attribute
from nlxpg.standards import Standards, resolve_attribute, resolve_entity

log = logging.getLogger(__name__)

SURROGATE_TYPE = "bigint"


def design_schema(ir: SchemaIR, *, standards: Standards | None = None) -> SchemaIR:
    ir = ir.model_copy(deep=True)
    if standards is not None:
        _apply_standards(ir, standards)
    rename = _normalize_names(ir)
    for e in ir.entities:
        _normalize_types(e)
        _assign_primary_key(e)
    ir.relationships = _normalize_relationships(ir, rename)
    _materialize_foreign_keys(ir)
    return ir


def _note_rename(notes: list[str], proposed: str | None, standard: str) -> None:
    """LLM이 낸 물리명을 표준 물리명으로 바꿀 때 근거를 남긴다(화면에서 표준 판정에 마우스를 올리면 보인다).
    대소문자만 다르면 남기지 않는다."""
    if proposed and proposed.lower() != standard.lower():
        notes.append(f"LLM 제안 '{proposed}' → 표준 '{standard}'")


def _apply_standards(ir: SchemaIR, std: Standards) -> None:
    """논리명을 표준용어로 맞추고 물리명·도메인·타입을 정한다. 표준으로 풀리지 않는
    이름은 추출 단계의 값을 그대로 두고 nonstandard로 표시한다."""
    ir.standards_version = std.version
    table_rename: dict[str, str] = {}
    for e in ir.entities:
        res = resolve_entity(std, e.logical_name)
        e.standard = res.status
        e.standard_notes = res.notes
        if res.physical_name:
            if res.logical_name != e.logical_name:
                e.standard_notes.append(f"논리명 '{e.logical_name}' → '{res.logical_name}'")
                e.aliases = list(dict.fromkeys([*e.aliases, e.logical_name]))
                e.logical_name = res.logical_name
            _note_rename(e.standard_notes, e.physical_name, res.physical_name)
            table_rename[e.physical_name] = res.physical_name
            e.physical_name = res.physical_name

        kept: dict[str, Attribute] = {}
        for a in e.attributes:
            res = resolve_attribute(
                std, a.logical_name, type_hint=a.data_type, examples=a.value_examples
            )
            a.standard = res.status
            a.standard_notes = res.notes
            if res.physical_name is not None:
                if res.logical_name != a.logical_name:
                    a.standard_notes.append(f"논리명 '{a.logical_name}' → '{res.logical_name}'")
                    a.logical_name = res.logical_name
                _note_rename(a.standard_notes, a.physical_name, res.physical_name)
                a.physical_name = res.physical_name
                a.domain = res.domain
                if res.data_type:
                    a.data_type = res.data_type
            # "거래처명"과 "거래처 이름"처럼 표준 적용 후 같은 용어가 된 속성은 합친다.
            if a.physical_name in kept:
                merge_attribute(kept[a.physical_name], a)
            else:
                kept[a.physical_name] = a
        e.attributes = list(kept.values())

    for r in ir.relationships:
        r.from_entity = table_rename.get(r.from_entity, r.from_entity)
        r.to_entity = table_rename.get(r.to_entity, r.to_entity)


def _normalize_names(ir: SchemaIR) -> dict[str, str]:
    rename: dict[str, str] = {}
    tables: set[str] = set()
    for e in ir.entities:
        new = unique_name(to_identifier(e.physical_name, fallback="t"), tables)
        tables.add(new)
        rename.setdefault(e.physical_name, new)
        e.physical_name = new

        cols: set[str] = set()
        for a in e.attributes:
            a.physical_name = unique_name(to_identifier(a.physical_name), cols)
            cols.add(a.physical_name)
    return rename


def _normalize_types(e: Entity) -> None:
    for a in e.attributes:
        if a.domain:  # 표준도메인에서 정한 타입은 그대로 둔다
            continue
        hinted = normalize_type(a.data_type)
        if hinted == "text" and a.data_type.strip().lower() in ("", "text"):
            hinted = infer_from_examples(a.value_examples) or "text"
        a.data_type = hinted


def _assign_primary_key(e: Entity) -> None:
    pk = [a.physical_name for a in e.attributes if a.is_primary_key]
    if not pk:
        pk = [c for c in e.primary_key if e.attribute(c)]
    if not pk:
        surrogate = surrogate_key_name(e.physical_name)
        existing = e.attribute(surrogate)
        if existing is None:
            e.attributes.insert(
                0,
                Attribute(
                    logical_name=f"{e.logical_name} ID",
                    physical_name=surrogate,
                    description=f"{e.logical_name} 대리키",
                    data_type=SURROGATE_TYPE,
                ),
            )
        pk = [surrogate]
    for a in e.attributes:
        a.is_primary_key = a.physical_name in pk
        if a.is_primary_key:
            a.nullable = False
    e.primary_key = pk


def _normalize_relationships(ir: SchemaIR, rename: dict[str, str]) -> list[Relationship]:
    known = {e.physical_name for e in ir.entities}
    out: list[Relationship] = []
    for r in ir.relationships:
        src = rename.get(r.from_entity, to_identifier(r.from_entity, fallback="t"))
        dst = rename.get(r.to_entity, to_identifier(r.to_entity, fallback="t"))
        if src not in known or dst not in known:
            log.warning("관계 %s 제거: 엔티티 없음 (%s → %s)", r.name, src, dst)
            continue
        if r.cardinality == "N:1":
            src, dst = dst, src
            r.cardinality = "1:N"
        r.from_entity, r.to_entity = src, dst
        r.name = to_identifier(r.name, fallback="rel")
        out.append(r)
    return out


def _materialize_foreign_keys(ir: SchemaIR) -> None:
    for r in list(ir.relationships):
        parent = ir.entity(r.from_entity)
        child = ir.entity(r.to_entity)
        assert parent and child
        if r.cardinality == "N:M":
            _junction(ir, r, parent, child)
        else:
            cols = _add_fk_columns(child, parent, unique=r.cardinality == "1:1",
                                   self_ref=parent is child)
            r.foreign_key = ForeignKey(table=child.physical_name, columns=cols,
                                       ref_columns=list(parent.primary_key))


def _add_fk_columns(child: Entity, parent: Entity, *, unique: bool, self_ref: bool) -> list[str]:
    cols: list[str] = []
    taken = {a.physical_name for a in child.attributes}
    for pk_col in parent.primary_key:
        pk_attr = parent.attribute(pk_col)
        assert pk_attr
        name = f"parent_{pk_col}" if self_ref else pk_col
        existing = child.attribute(name)
        if existing is not None and not existing.is_primary_key:
            # 추출 단계가 이미 "고객번호" 같은 참조 컬럼을 뽑아 둔 경우 — 재사용하고 타입만 맞춘다.
            existing.data_type = pk_attr.data_type
            existing.is_unique = existing.is_unique or unique
            cols.append(name)
            continue
        if existing is not None:  # 자식 PK와 이름이 겹침 (1:1 공유키 등)
            name = unique_name(f"{parent.physical_name}_{pk_col}"[:63], taken)
        child.attributes.append(
            Attribute(
                # 부모 PK의 논리명을 그대로 쓴다(대리키면 이미 "거래처 ID"처럼 부모 이름을 담고 있다).
                logical_name=f"상위{pk_attr.logical_name}" if self_ref else pk_attr.logical_name,
                physical_name=name,
                description=f"{parent.logical_name} 참조",
                data_type=pk_attr.data_type,
                is_unique=unique,
            )
        )
        taken.add(name)
        cols.append(name)
    return cols


def _junction(ir: SchemaIR, r: Relationship, a: Entity, b: Entity) -> None:
    taken = {e.physical_name for e in ir.entities}
    name = unique_name(f"{a.physical_name}_{b.physical_name}"[:63], taken)
    j = Entity(
        logical_name=f"{a.logical_name}-{b.logical_name}",
        physical_name=name,
        description=r.description or f"{a.logical_name}와 {b.logical_name}의 N:M 연결",
        evidence=r.evidence,
    )
    ir.entities.append(j)
    cols_a = _add_fk_columns(j, a, unique=False, self_ref=False)
    cols_b = _add_fk_columns(j, b, unique=False, self_ref=a is b)
    for attr in j.attributes:
        attr.nullable = False
        attr.is_primary_key = True
    j.primary_key = cols_a + cols_b

    # 원래 N:M 관계를 연결 테이블을 거치는 1:N 두 개로 바꾼다.
    ir.relationships.remove(r)
    for parent, cols in ((a, cols_a), (b, cols_b)):
        ir.relationships.append(
            Relationship(
                name=r.name, from_entity=parent.physical_name, to_entity=name, cardinality="1:N",
                description=r.description, evidence=r.evidence,
                foreign_key=ForeignKey(table=name, columns=cols, ref_columns=list(parent.primary_key)),
            )
        )
