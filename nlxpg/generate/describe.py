"""IR → 테이블·컬럼 설명서(마크다운). 사람 검토 시 문서·스키마·근거를 나란히 보는 용도."""
from __future__ import annotations

from nlxpg.ir import SchemaIR

_STATUS = {"common": "공통", "composed": "조합", "nonstandard": "비표준"}


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def to_markdown(ir: SchemaIR) -> str:
    out = ["# 스키마 설명", ""]
    if ir.standards_version:
        legend = "공통=공통표준용어, 조합=표준단어 조합(DB표준 후보), 비표준=표준단어로 분할되지 않음"
        out += [f"공통표준 {ir.standards_version}판 적용 (ADR-0005). 표준: {legend}", ""]
    if ir.source_documents:
        out += ["원천 문서: " + ", ".join(f"{d.title} ({d.doc_id})" for d in ir.source_documents), ""]
    for e in ir.entities:
        out.append(f"## {e.logical_name} (`{e.physical_name}`)")
        out.append("")
        if e.description:
            out += [e.description, ""]
        if e.aliases:
            out += [f"별칭: {', '.join(e.aliases)}", ""]
        out += ["| 논리명 | 물리명 | 타입 | 도메인 | 표준 | 키 | NULL | 설명 | 근거 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for a in e.attributes:
            key = "PK" if a.is_primary_key else ("UK" if a.is_unique else "")
            ev = "; ".join(f"{x.doc_id}: {x.span}" for x in a.evidence)
            std = _STATUS.get(a.standard or "", "")
            if a.standard_notes:
                std += " (" + "; ".join(a.standard_notes) + ")"
            out.append(
                f"| {_cell(a.logical_name)} | `{a.physical_name}` | {a.data_type} | {a.domain or ''} | "
                f"{_cell(std)} | {key} | {'Y' if a.nullable else 'N'} | {_cell(a.description)} | {_cell(ev)} |"
            )
        out.append("")
    if ir.relationships:
        out += ["## 관계", "", "| 관계 | 부모 | 자식 | 카디널리티 | FK | 근거 |",
                "| --- | --- | --- | --- | --- | --- |"]
        for r in ir.relationships:
            fk = f"{r.foreign_key.table}({', '.join(r.foreign_key.columns)})" if r.foreign_key else ""
            ev = "; ".join(f"{x.doc_id}: {x.span}" for x in r.evidence)
            out.append(f"| {r.name} | {r.from_entity} | {r.to_entity} | {r.cardinality} | {fk} | {_cell(ev)} |")
        out.append("")
    return "\n".join(out)
