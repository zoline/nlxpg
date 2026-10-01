"""정답 스키마 로드. BIRD는 pgxnl에서 PostgreSQL로 이관해 두었으므로 카탈로그에서 바로 읽는다."""
from __future__ import annotations

import asyncpg

from nlxpg.ir import Attribute, Entity, ForeignKey, Relationship, SchemaIR

_COLUMNS = """
SELECT c.table_name, c.column_name, c.is_nullable = 'YES' AS nullable,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(a.attrelid, a.attnum) AS comment
FROM information_schema.columns c
JOIN pg_attribute a
  ON a.attrelid = format('%I.%I', c.table_schema, c.table_name)::regclass
 AND a.attname = c.column_name
WHERE c.table_schema = $1
ORDER BY c.table_name, c.ordinal_position
"""

_CONSTRAINTS = """
SELECT con.contype, cl.relname AS table_name, ref.relname AS ref_table,
       array(SELECT attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute ON attrelid = con.conrelid AND attnum = k.n ORDER BY k.i) AS cols,
       array(SELECT attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute ON attrelid = con.confrelid AND attnum = k.n ORDER BY k.i) AS ref_cols
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = cl.relnamespace
LEFT JOIN pg_class ref ON ref.oid = con.confrelid
WHERE ns.nspname = $1 AND con.contype IN ('p', 'f', 'u')
"""


async def schema_from_database(dsn: str, schema: str = "public") -> SchemaIR:
    conn = await asyncpg.connect(dsn)
    try:
        cols = await conn.fetch(_COLUMNS, schema)
        cons = await conn.fetch(_CONSTRAINTS, schema)
    finally:
        await conn.close()

    entities: dict[str, Entity] = {}
    for r in cols:
        e = entities.setdefault(
            r["table_name"], Entity(logical_name=r["table_name"], physical_name=r["table_name"])
        )
        e.attributes.append(Attribute(
            logical_name=r["column_name"], physical_name=r["column_name"],
            description=r["comment"] or "", data_type=r["data_type"], nullable=r["nullable"],
        ))

    relationships: list[Relationship] = []
    for c in cons:
        e = entities.get(c["table_name"])
        if e is None:
            continue
        if c["contype"] == "p":
            e.primary_key = list(c["cols"])
            for a in e.attributes:
                a.is_primary_key = a.physical_name in e.primary_key
        elif c["contype"] == "u" and len(c["cols"]) == 1:
            attr = e.attribute(c["cols"][0])
            if attr:
                attr.is_unique = True
        elif c["contype"] == "f":
            relationships.append(Relationship(
                name=f"{c['table_name']}_{c['ref_table']}",
                from_entity=c["ref_table"], to_entity=c["table_name"], cardinality="1:N",
                foreign_key=ForeignKey(table=c["table_name"], columns=list(c["cols"]),
                                       ref_columns=list(c["ref_cols"])),
            ))
    return SchemaIR(entities=list(entities.values()), relationships=relationships)
