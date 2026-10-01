"""엔티티 정합 베이스라인.

plan.md는 이 단계를 "가장 어려운 단계"로 본다. 목표 구현은 임베딩 유사도 클러스터링과
LLM 판정, 동의어 사전의 조합이다. 지금은 이름·별칭이 정규화 후 일치하는 경우만 합친다.
나중에 붙일 판정기는 `same_entity` 자리에 끼워 넣는다.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from nlxpg.ir import Attribute, Entity, Evidence, Relationship, SchemaIR

SameEntityFn = Callable[[Entity, Entity], bool]


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-()]+", "", name).lower()


def _keys(e: Entity) -> set[str]:
    return {_norm(n) for n in (e.physical_name, e.logical_name, *e.aliases) if n}


def same_by_name(a: Entity, b: Entity) -> bool:
    return bool(_keys(a) & _keys(b))


def merge_irs(irs: list[SchemaIR]) -> SchemaIR:
    """문서별 부분 IR을 단순히 이어 붙인다. 통합은 reconcile이 한다."""
    out = SchemaIR()
    for ir in irs:
        out.source_documents += ir.source_documents
        out.entities += ir.entities
        out.relationships += ir.relationships
    return out


def reconcile(ir: SchemaIR, *, same_entity: SameEntityFn = same_by_name) -> SchemaIR:
    merged: list[Entity] = []
    rename: dict[str, str] = {}  # 합쳐진 엔티티의 physical_name → 대표 physical_name

    for e in ir.entities:
        target = next((m for m in merged if same_entity(m, e)), None)
        if target is None:
            merged.append(e.model_copy(deep=True))
            rename[e.physical_name] = e.physical_name
            continue
        rename[e.physical_name] = target.physical_name
        _merge_entity(target, e)

    relationships = _dedupe_relationships(
        [
            r.model_copy(update={
                "from_entity": rename.get(r.from_entity, r.from_entity),
                "to_entity": rename.get(r.to_entity, r.to_entity),
            })
            for r in ir.relationships
        ]
    )
    return SchemaIR(
        schema_version=ir.schema_version,
        source_documents=ir.source_documents,
        entities=merged,
        relationships=relationships,
    )


def _merge_entity(target: Entity, other: Entity) -> None:
    names = {target.physical_name, target.logical_name, *target.aliases}
    target.aliases += [
        n for n in (other.logical_name, other.physical_name, *other.aliases) if n not in names
    ]
    target.aliases = list(dict.fromkeys(target.aliases))
    if len(other.description) > len(target.description):
        target.description = other.description
    target.evidence = _union_evidence(target.evidence, other.evidence)

    for a in other.attributes:
        existing = next(
            (x for x in target.attributes
             if _norm(x.physical_name) == _norm(a.physical_name)
             or _norm(x.logical_name) == _norm(a.logical_name)),
            None,
        )
        if existing is None:
            target.attributes.append(a.model_copy(deep=True))
        else:
            merge_attribute(existing, a)


def merge_attribute(target: Attribute, other: Attribute) -> None:
    target.value_examples = list(dict.fromkeys(target.value_examples + other.value_examples))
    target.evidence = _union_evidence(target.evidence, other.evidence)
    # 한 곳이라도 필수라고 했으면 필수, 식별자라고 했으면 식별자.
    target.nullable = target.nullable and other.nullable
    target.is_unique = target.is_unique or other.is_unique
    if target.data_type == "text" and other.data_type != "text":
        target.data_type = other.data_type
    if len(other.description) > len(target.description):
        target.description = other.description


def _union_evidence(a: list[Evidence], b: list[Evidence]) -> list[Evidence]:
    seen = {(e.doc_id, e.span) for e in a}
    return a + [e for e in b if (e.doc_id, e.span) not in seen]


def _dedupe_relationships(rels: list[Relationship]) -> list[Relationship]:
    out: dict[tuple[str, str], Relationship] = {}
    for r in rels:
        if r.from_entity == r.to_entity and r.cardinality == "1:1":
            continue  # 합쳐진 결과 자기 자신과의 1:1이 된 관계는 의미가 없다
        key = tuple(sorted((r.from_entity, r.to_entity)))
        if key in out:
            out[key].evidence = _union_evidence(out[key].evidence, r.evidence)
        else:
            out[key] = r  # type: ignore[index]
    return list(out.values())
