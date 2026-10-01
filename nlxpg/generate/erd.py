"""IR → Mermaid erDiagram (사람 검토용)."""
from __future__ import annotations

import re

from nlxpg.ir import SchemaIR

_CARD = {"1:1": "||--o|", "1:N": "||--o{", "N:M": "}o--o{"}


def _type(t: str) -> str:
    # Mermaid 속성 타입에는 공백·괄호·쉼표를 쓸 수 없다: varchar(100) → varchar_100
    return re.sub(r"[^0-9A-Za-z_]+", "_", t).strip("_") or "text"


def _label(s: str) -> str:
    return s.replace('"', "'")


def to_mermaid(ir: SchemaIR) -> str:
    lines = ["erDiagram"]
    for e in ir.entities:
        fk_cols = {
            c for r in ir.relationships if r.foreign_key and r.foreign_key.table == e.physical_name
            for c in r.foreign_key.columns
        }
        lines.append(f"    {e.physical_name} {{")
        for a in e.attributes:
            keys = [k for k, on in (("PK", a.is_primary_key), ("FK", a.physical_name in fk_cols),
                                    ("UK", a.is_unique and not a.is_primary_key)) if on]
            key = f" {','.join(keys)}" if keys else ""
            lines.append(f'        {_type(a.data_type)} {a.physical_name}{key} "{_label(a.logical_name)}"')
        lines.append("    }")
    for r in ir.relationships:
        lines.append(
            f'    {r.from_entity} {_CARD.get(r.cardinality, "||--o{")} {r.to_entity} : "{_label(r.name)}"'
        )
    return "\n".join(lines) + "\n"
