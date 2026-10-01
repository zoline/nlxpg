"""규칙 기반 IR 린트.

DDL 문법 검증은 샌드박스 실행이 맡는다. 여기서는 실행은 되지만 설계상 문제가 있는 것을
잡는다. pgxnl의 PostgreSQL ANTLR4 문법을 붙이는 것은 3단계에서 검토한다(ADR-0004).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nlxpg.ir import SchemaIR
from nlxpg.modeling.naming import MAX_LEN, RESERVED


@dataclass
class LintIssue:
    level: Literal["error", "warning"]
    target: str
    message: str


def lint_schema(ir: SchemaIR) -> list[LintIssue]:
    issues: list[LintIssue] = []
    tables = [e.physical_name for e in ir.entities]
    for dup in {t for t in tables if tables.count(t) > 1}:
        issues.append(LintIssue("error", dup, "테이블명 중복"))

    for e in ir.entities:
        t = e.physical_name
        if not e.primary_key:
            issues.append(LintIssue("error", t, "PK 없음"))
        if not e.attributes:
            issues.append(LintIssue("error", t, "컬럼 없음"))
        cols = [a.physical_name for a in e.attributes]
        for dup in {c for c in cols if cols.count(c) > 1}:
            issues.append(LintIssue("error", f"{t}.{dup}", "컬럼명 중복"))
        for name in (t, *cols):
            if len(name) > MAX_LEN:
                issues.append(LintIssue("error", name, f"식별자 길이 {MAX_LEN}자 초과"))
            if name in RESERVED:
                issues.append(LintIssue("error", name, "예약어"))
        if not e.evidence:
            issues.append(LintIssue("warning", t, "근거(evidence) 없음"))
        for a in e.attributes:
            target = f"{t}.{a.physical_name}"
            if a.data_type == "text" and not a.value_examples and not a.is_primary_key:
                issues.append(LintIssue("warning", target, "타입 근거 없이 text"))
            # 공통표준 (ADR-0005). 대리키·FK처럼 모델링이 추가한 컬럼은 standard가 None이다.
            if a.standard == "nonstandard":
                issues.append(LintIssue("warning", target, f"비표준 용어 '{a.logical_name}'"))
            for note in a.standard_notes:
                if note.startswith(("금칙어", "마지막 단어", "영문약어명")):
                    issues.append(LintIssue("warning", target, note))
        if e.standard == "nonstandard":
            issues.append(LintIssue("warning", t, f"비표준 엔터티명 '{e.logical_name}'"))

    for r in ir.relationships:
        fk = r.foreign_key
        if fk is None:
            issues.append(LintIssue("error", r.name, "FK로 구체화되지 않은 관계"))
            continue
        parent, child = ir.entity(r.from_entity), ir.entity(fk.table)
        if parent is None or child is None:
            issues.append(LintIssue("error", r.name, "관계가 없는 테이블을 가리킴"))
            continue
        ref_cols = fk.ref_columns or parent.primary_key
        for c, rc in zip(fk.columns, ref_cols, strict=False):
            ca, pa = child.attribute(c), parent.attribute(rc)
            if ca is None or pa is None:
                issues.append(LintIssue("error", f"{fk.table}.{c}", "FK 컬럼 없음"))
            elif ca.data_type != pa.data_type:
                issues.append(LintIssue(
                    "error", f"{fk.table}.{c}", f"FK 타입 불일치 {ca.data_type} ≠ {pa.data_type}"
                ))
    return issues
