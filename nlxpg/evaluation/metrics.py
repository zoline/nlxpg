"""테이블·컬럼 매칭 P/R/F1, PK/FK 일치율, 타입 정확도.

이름은 정답과 다르게 나오는 경우가 많아(customer ↔ client) 매칭은 의미 기반이어야 한다
(ADR-0003). 매칭 함수는 교체할 수 있게 두고, 지금은 정규화 문자열 일치(exact_matcher)만
제공한다. 임베딩·LLM 판정 매처는 1단계에서 수작업 판정과의 일치율을 확인한 뒤 추가한다.

매칭은 1:1이다 — 점수 행렬에서 탐욕적으로 가장 높은 쌍부터 고른다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

from nlxpg.ir import Entity, SchemaIR

#: (예측 이름들, 정답 이름들) → 0~1 유사도. 이름들 = physical, logical, aliases.
Matcher = Callable[[list[str], list[str]], float]


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def exact_matcher(pred: list[str], gold: list[str]) -> float:
    return 1.0 if {_norm(p) for p in pred} & {_norm(g) for g in gold} else 0.0


@dataclass
class PRF:
    precision: float
    recall: float
    f1: float
    matched: int
    predicted: int
    gold: int

    @classmethod
    def of(cls, matched: int, predicted: int, gold: int) -> PRF:
        p = matched / predicted if predicted else 0.0
        r = matched / gold if gold else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return cls(round(p, 4), round(r, 4), round(f, 4), matched, predicted, gold)


@dataclass
class SchemaScore:
    table: PRF
    column: PRF
    pk_match_rate: float
    fk: PRF
    type_accuracy: float
    table_pairs: list[tuple[str, str]]

    def to_dict(self) -> dict:
        return asdict(self)


def _greedy(scores: list[tuple[float, int, int]], threshold: float) -> list[tuple[int, int]]:
    used_p: set[int] = set()
    used_g: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for s, i, j in sorted(scores, reverse=True):
        if s < threshold or i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pairs.append((i, j))
    return pairs


def _names_e(e: Entity) -> list[str]:
    return [e.physical_name, e.logical_name, *e.aliases]


def _base_type(t: str) -> str:
    t = t.lower().split("(")[0].strip()
    return {"character varying": "varchar", "character": "char", "int4": "integer",
            "int8": "bigint", "timestamp without time zone": "timestamp",
            "timestamp with time zone": "timestamptz"}.get(t, t)


def score_schema(
    pred: SchemaIR, gold: SchemaIR, *, matcher: Matcher = exact_matcher, threshold: float = 0.5
) -> SchemaScore:
    t_scores = [
        (matcher(_names_e(p), _names_e(g)), i, j)
        for i, p in enumerate(pred.entities) for j, g in enumerate(gold.entities)
    ]
    t_pairs = _greedy(t_scores, threshold)

    col_matched = 0
    type_ok = 0
    pk_ok = 0
    col_map: dict[tuple[str, str], tuple[str, str]] = {}  # (pred_t, pred_c) → (gold_t, gold_c)
    for i, j in t_pairs:
        p, g = pred.entities[i], gold.entities[j]
        c_scores = [
            (matcher([a.physical_name, a.logical_name], [b.physical_name, b.logical_name]), x, y)
            for x, a in enumerate(p.attributes) for y, b in enumerate(g.attributes)
        ]
        c_pairs = _greedy(c_scores, threshold)
        col_matched += len(c_pairs)
        for x, y in c_pairs:
            a, b = p.attributes[x], g.attributes[y]
            col_map[(p.physical_name, a.physical_name)] = (g.physical_name, b.physical_name)
            type_ok += _base_type(a.data_type) == _base_type(b.data_type)
        mapped_pk = {col_map.get((p.physical_name, c)) for c in p.primary_key}
        if mapped_pk == {(g.physical_name, c) for c in g.primary_key}:
            pk_ok += 1

    def fk_set(ir: SchemaIR, mapping: dict | None) -> set:
        out = set()
        for r in ir.relationships:
            if not r.foreign_key:
                continue
            cols = tuple((r.foreign_key.table, c) for c in r.foreign_key.columns)
            if mapping is not None:
                cols = tuple(mapping.get(c) for c in cols)  # type: ignore[misc]
            out.add(cols)
        return out

    pred_fk, gold_fk = fk_set(pred, col_map), fk_set(gold, None)
    return SchemaScore(
        table=PRF.of(len(t_pairs), len(pred.entities), len(gold.entities)),
        column=PRF.of(
            col_matched,
            sum(len(e.attributes) for e in pred.entities),
            sum(len(e.attributes) for e in gold.entities),
        ),
        pk_match_rate=round(pk_ok / len(t_pairs), 4) if t_pairs else 0.0,
        fk=PRF.of(len(pred_fk & gold_fk), len(pred_fk), len(gold_fk)),
        type_accuracy=round(type_ok / col_matched, 4) if col_matched else 0.0,
        table_pairs=[(pred.entities[i].physical_name, gold.entities[j].physical_name) for i, j in t_pairs],
    )
