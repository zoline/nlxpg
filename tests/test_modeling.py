from nlxpg.modeling import design_schema, normalize_type, to_identifier
from nlxpg.modeling.types import infer_from_examples


def test_identifier():
    assert to_identifier("customerName") == "customer_name"
    assert to_identifier("Order") == "order_"
    assert to_identifier("1st item") == "col_1st_item"
    assert to_identifier("고객") == "col"


def test_reserved_words_include_type_func_category():
    """run 8: '좋아요' → like 가 DDL을 깨뜨렸다. T 범주(like, join, left, is ...)도 예약어다."""
    from nlxpg.modeling.naming import RESERVED

    assert len(RESERVED) == 101
    for w in ("like", "join", "left", "is", "full", "cross"):
        assert to_identifier(w) == w + "_"


def test_types():
    assert normalize_type("VARCHAR(100)") == "varchar(100)"
    assert normalize_type("int") == "integer"
    assert normalize_type("decimal(10, 2)") == "numeric(10,2)"
    assert normalize_type("strange") == "text"
    assert infer_from_examples(["2026-09-30"]) == "date"
    assert infer_from_examples(["00123"]) == "varchar(5)"
    assert infer_from_examples(["12", "300"]) == "integer"


def test_design(order_ir):
    ir = design_schema(order_ir)
    names = [e.physical_name for e in ir.entities]
    assert names[:4] == ["customer", "order_", "product", "category"]

    cust = ir.entity("customer")
    assert cust.primary_key == ["customer_id"]
    assert cust.attribute("customer_name").nullable is False

    order = ir.entity("order_")
    assert order.attribute("customer_id").data_type == "bigint"
    assert order.attribute("order_date").data_type == "date"
    assert order.primary_key == ["order_id"]  # order__id가 아니다

    # N:M → 연결 테이블 + 1:N 두 개
    junction = ir.entity("product_category")
    assert junction.primary_key == ["product_id", "category_id"]
    rels = {(r.from_entity, r.to_entity, r.cardinality) for r in ir.relationships}
    assert ("product", "product_category", "1:N") in rels
    assert ("category", "product_category", "1:N") in rels
    assert all(r.foreign_key for r in ir.relationships)


def test_n_to_1_is_flipped(order_ir):
    order_ir.relationships[0].from_entity, order_ir.relationships[0].to_entity = "order", "Customer"
    order_ir.relationships[0].cardinality = "N:1"
    ir = design_schema(order_ir)
    r = ir.relationships[0]
    assert (r.from_entity, r.to_entity, r.cardinality) == ("customer", "order_", "1:N")
