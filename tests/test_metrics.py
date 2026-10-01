from nlxpg.evaluation import score_schema
from nlxpg.ir import Attribute, Entity, ForeignKey, Relationship, SchemaIR


def _ir(tables: dict[str, list[str]], fks=()) -> SchemaIR:
    ents = []
    for t, cols in tables.items():
        ents.append(Entity(logical_name=t, physical_name=t, primary_key=[cols[0]], attributes=[
            Attribute(logical_name=c, physical_name=c, data_type="integer", is_primary_key=i == 0)
            for i, c in enumerate(cols)
        ]))
    rels = [Relationship(name="r", from_entity=p, to_entity=c,
                         foreign_key=ForeignKey(table=c, columns=[col])) for p, c, col in fks]
    return SchemaIR(entities=ents, relationships=rels)


def test_perfect():
    g = _ir({"customer": ["customer_id", "name"], "orders": ["order_id", "customer_id"]},
            [("customer", "orders", "customer_id")])
    s = score_schema(g, g)
    assert s.table.f1 == s.column.f1 == s.fk.f1 == 1.0
    assert s.pk_match_rate == 1.0 and s.type_accuracy == 1.0


def test_partial():
    g = _ir({"customer": ["customer_id", "name"], "orders": ["order_id", "customer_id"]})
    p = _ir({"customer": ["customer_id", "name", "grade"], "invoice": ["invoice_id"]})
    s = score_schema(p, g)
    assert s.table.matched == 1 and s.table.precision == 0.5 and s.table.recall == 0.5
    assert s.column.matched == 2
