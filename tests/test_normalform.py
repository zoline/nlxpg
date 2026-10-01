import pytest

from nlxpg.ir import Attribute, Entity, ForeignKey, Relationship, SchemaIR
from nlxpg.standards import load_standards
from nlxpg.validate.normalform import check_normal_forms
from tests.test_standards import STD_DIR


@pytest.fixture(scope="module")
def std():
    return load_standards(STD_DIR)


def attr(logical, physical, dtype="varchar(100)", pk=False, **kw):
    return Attribute(logical_name=logical, physical_name=physical, data_type=dtype, is_primary_key=pk, **kw)


def ent(logical, physical, *attrs, pk=None):
    return Entity(logical_name=logical, physical_name=physical, attributes=list(attrs),
                  primary_key=pk or [attrs[0].physical_name])


def fk(parent, child, cols):
    return Relationship(name="r", from_entity=parent, to_entity=child,
                        foreign_key=ForeignKey(table=child, columns=cols))


def kinds(findings):
    return {(f.kind, f.level, f.table) for f in findings}


def order_schema(*extra_item_attrs):
    return SchemaIR(
        entities=[
            ent("상품", "gds", attr("상품 ID", "gds_id", "bigint", pk=True), attr("상품코드", "gds_cd"),
                attr("상품명", "gds_nm")),
            ent("주문", "ordr", attr("주문 ID", "ordr_id", "bigint", pk=True),
                attr("주문총금액", "ordr_gramt", "numeric(15)")),
            ent("주문품목", "ordr_item", attr("주문품목 ID", "ordr_item_id", "bigint", pk=True),
                attr("수량", "qty", "numeric(10)"), attr("단가", "untprc", "numeric(15)"),
                attr("주문 ID", "ordr_id", "bigint"), attr("상품 ID", "gds_id", "bigint"), *extra_item_attrs),
        ],
        relationships=[fk("ordr", "ordr_item", ["ordr_id"]), fk("gds", "ordr_item", ["gds_id"])],
    )


def test_clean_schema_only_flags_derived_total(std):
    assert kinds(check_normal_forms(order_schema(), std)) == {("derived", "review", "ordr")}


def test_foreign_copy_through_fk_is_violation(std):
    found = check_normal_forms(order_schema(attr("상품명", "gds_nm")), std)
    assert ("foreign_copy", "violation", "ordr_item") in kinds(found)


def test_foreign_copy_without_fk_is_review(std):
    ir = order_schema(attr("상품명", "gds_nm"))
    ir.relationships = [r for r in ir.relationships if r.from_entity != "gds"]
    found = {(f.kind, f.columns[0], f.level) for f in check_normal_forms(ir, std)}
    # FK가 없어지면 상품 ID는 다른 테이블 PK와 이름만 같은 일반 컬럼이 된다 → 관계 누락
    assert ("foreign_copy", "gds_nm", "review") in found
    assert ("missing_fk", "gds_id", "review") in found


def test_repeating_group(std):
    ir = SchemaIR(entities=[ent("거래처", "cnpt", attr("거래처 ID", "cnpt_id", "bigint", pk=True),
                                attr("전화번호1", "telno1"), attr("전화번호2", "telno2"))])
    f = check_normal_forms(ir, std)
    assert kinds(f) == {("repeating_group", "violation", "cnpt")}
    assert f[0].columns == ["telno1", "telno2"]


def test_multivalued_example_but_not_thousands(std):
    ir = SchemaIR(entities=[ent("거래처", "cnpt", attr("거래처 ID", "cnpt_id", "bigint", pk=True),
                                attr("취급품목", "item", value_examples=["사과, 배, 감"]),
                                attr("금액", "amt", "numeric(15)", value_examples=["1,000,000"]))])
    assert [f.columns for f in check_normal_forms(ir, std)] == [["item"]]


def test_partial_dependency_in_junction(std):
    ir = SchemaIR(
        entities=[
            ent("상품", "gds", attr("상품 ID", "gds_id", "bigint", pk=True)),
            ent("범주", "ctgry", attr("범주 ID", "ctgry_id", "bigint", pk=True)),
            ent("상품-범주", "gds_ctgry", attr("상품 ID", "gds_id", "bigint", pk=True),
                attr("범주 ID", "ctgry_id", "bigint", pk=True), attr("상품명", "gds_nm"),
                attr("등록일자", "reg_ymd", "date"), pk=["gds_id", "ctgry_id"]),
        ],
        relationships=[fk("gds", "gds_ctgry", ["gds_id"]), fk("ctgry", "gds_ctgry", ["ctgry_id"])],
    )
    found = check_normal_forms(ir, std)
    partial = [f for f in found if f.kind == "partial_dependency"]
    assert [f.columns for f in partial] == [["gds_nm"]]  # 등록일자는 관계 자체의 속성이라 해당 없음


def test_code_name_pair_except_own_identifier(std):
    ir = SchemaIR(entities=[ent("거래처", "cnpt", attr("거래처 ID", "cnpt_id", "bigint", pk=True),
                                attr("거래처코드", "cnpt_cd"), attr("거래처명", "cnpt_nm"),
                                attr("지역코드", "rgn_cd"), attr("지역명", "rgn_nm"))])
    found = check_normal_forms(ir, std)
    assert [f.columns for f in found if f.kind == "code_name_pair"] == [["rgn_cd", "rgn_nm"]]


def test_generic_names_are_not_copies_and_counts_are_derived(std):
    """실측(run 6): 내용·작성일시는 여러 테이블이 각자 갖는다. 좋아요수는 사본이 아니라 행 수다."""
    ir = SchemaIR(
        entities=[
            ent("게시글", "post", attr("게시글ID", "post_id", "bigint", pk=True), attr("내용", "cn"),
                attr("작성일시", "wrt_dt", "timestamp"), attr("댓글수", "cmnt_cnt", "numeric(10)")),
            ent("댓글", "cmnt", attr("댓글아이디", "cmnt_id", "bigint", pk=True), attr("내용", "cn"),
                attr("작성일시", "wrt_dt", "timestamp"), attr("게시글ID", "post_id", "bigint")),
        ],
        relationships=[fk("post", "cmnt", ["post_id"])],
    )
    found = check_normal_forms(ir, std)
    assert {(f.kind, f.table, f.columns[0]) for f in found} == {("derived", "post", "cmnt_cnt")}
