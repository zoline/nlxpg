import csv
from pathlib import Path

import pytest

from nlxpg.generate import to_ddl
from nlxpg.modeling import design_schema
from nlxpg.standards import load_standards, resolve_attribute, resolve_entity
from nlxpg.validate import lint_schema

STD_DIR = Path(__file__).parent.parent / "data" / "standards"


@pytest.fixture(scope="module")
def std():
    return load_standards(STD_DIR)


# ── 실제 공통표준 (data/standards) ────────────────────────────────


@pytest.mark.parametrize(("name", "status", "physical", "domain", "pg"), [
    ("거래처명", "common", "cnpt_nm", "명V100", "varchar(100)"),
    ("거래처 이름", "common", "cnpt_nm", "명V100", "varchar(100)"),  # 용어 이음동의어
    ("사업자번호", "common", "brno", "사업자등록번호C10", "char(10)"),  # 금칙어 → 대표단어
    ("주문일자", "common", "ordr_ymd", "연월일C8", "date"),  # ADR-0005: 연월일 → date
    ("등록일시", "common", "reg_dt", "연월일시분초D", "timestamp"),
    ("상품코드", "composed", "gds_cd", None, None),
    ("쀍쀍", "nonstandard", None, None, None),
])
def test_resolve_attribute(std, name, status, physical, domain, pg):
    r = resolve_attribute(std, name)
    assert r.status == status
    assert r.physical_name == physical
    if domain:
        assert (r.domain, r.data_type) == (domain, pg)


def test_forbidden_word_is_reported(std):
    r = resolve_attribute(std, "사업자번호")
    assert r.logical_name == "사업자등록번호"
    assert any(n.startswith("금칙어") for n in r.notes)


def test_composed_domain_follows_format_word_and_hint(std):
    r = resolve_attribute(std, "상품코드")
    assert r.domain.startswith("코드C") and r.data_type.startswith("char(")
    # 힌트 길이 이상인 도메인 중 가장 짧은 것
    r = resolve_attribute(std, "상품코드", type_hint="varchar(5)")
    assert r.domain == "코드C5"
    # 값 예시가 더 길면 예시에 맞춘다 (P00001 → 6자리 이상)
    r = resolve_attribute(std, "상품코드", examples=["P00001"])
    assert r.domain == "코드C7"


def test_last_word_must_be_format_word(std):
    r = resolve_attribute(std, "거래처상품")
    assert r.status == "composed" and r.domain is None
    assert any("형식단어" in n for n in r.notes)


def test_entity_names(std):
    assert resolve_entity(std, "주문품목").physical_name == "ordr_item"
    r = resolve_entity(std, "카테고리")  # 범주의 이음동의어
    assert (r.logical_name, r.physical_name) == ("범주", "ctgry")


def test_design_with_standards(std, order_ir):
    for e, name in zip(order_ir.entities, ["거래처", "주문", "상품", "카테고리"], strict=True):
        e.logical_name = name
    order_ir.entities[2].attributes[0].logical_name = "상품코드"
    ir = design_schema(order_ir, standards=std)

    assert ir.standards_version == std.version
    assert [e.physical_name for e in ir.entities] == ["cnpt", "ordr", "gds", "ctgry", "gds_ctgry"]
    cnpt, ordr = ir.entity("cnpt"), ir.entity("ordr")
    assert cnpt.attribute("cnpt_nm").data_type == "varchar(100)"
    assert ordr.attribute("ordr_ymd").data_type == "date"
    assert ordr.attribute("cnpt_id").data_type == "bigint"  # FK는 부모 대리키를 따른다
    # LLM이 낸 물리명을 표준으로 바꾼 근거가 남는다
    assert "LLM 제안 'Customer' → 표준 'cnpt'" in cnpt.standard_notes
    assert "LLM 제안 'customerName' → 표준 'cnpt_nm'" in cnpt.attribute("cnpt_nm").standard_notes
    ddl = to_ddl(ir)
    assert "REFERENCES cnpt (cnpt_id)" in ddl
    assert not [i for i in lint_schema(ir) if i.level == "error"]


def test_attributes_collapsing_to_same_term_are_merged(std, order_ir):
    cust = order_ir.entities[0]
    cust.logical_name = "거래처"
    cust.attributes[0].logical_name = "거래처명"
    extra = cust.attributes[0].model_copy(update={"logical_name": "거래처 이름", "value_examples": ["가나상사"]})
    cust.attributes.append(extra)
    ir = design_schema(order_ir, standards=std)
    cnpt = ir.entity("cnpt")
    assert [a.physical_name for a in cnpt.attributes].count("cnpt_nm") == 1
    assert "가나상사" in cnpt.attribute("cnpt_nm").value_examples


# ── 로더 규칙 (작은 가짜 CSV) ──────────────────────────────────


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


REV = "개정구분명(폐기 또는 변경)"


def _fake(tmp_path: Path, version: str, term_rows: list[list[str]]) -> None:
    _write(tmp_path / f"공통표준도메인_{version}.csv",
           ["공통표준도메인그룹명", "공통표준도메인분류명", "공통표준도메인명", "공통표준도메인설명",
            "데이터타입", "데이터길이", "데이터소수점길이", "저장형식", "표현형식", "단위", "허용값",
            "제정차수", REV, "개정항목", "개정사유"],
           [["명칭", "명", "명V100", "", "VARCHAR", "100", "", "", "", "", "", "1차(2020-08)", "", "", ""],
            ["명칭", "명", "명V200", "", "VARCHAR", "200", "", "", "", "", "", "5차(2022-07)", "", "", ""]])
    _write(tmp_path / f"공통표준단어_{version}.csv",
           ["공통표준단어명", "공통표준단어영문약어명", "공통표준단어 영문명", "공통표준단어 설명",
            "형식단어여부", "공통표준도메인분류명", "이음동의어 목록", "금칙어 목록", "제정차수", REV,
            "개정항목", "개정사유"],
           [["명", "NM", "Name", "", "Y", "명", "이름", "", "1차(2020-08)", "", "", ""],
            ["상호", "CONM", "Company Name", "", "N", "", "", "", "1차(2020-08)", "", "", ""]])
    _write(tmp_path / f"공통표준용어_{version}.csv",
           ["공통표준용어명", "공통표준용어설명", "공통표준용어영문약어명", "공통표준도메인명", "허용값",
            "저장 형식", "표현 형식", "행정표준코드명", "소관기관명", "용어 이음동의어 목록", "제정차수",
            REV, "개정항목", "개정사유"],
           term_rows)


def test_loader_drops_retired_rows(tmp_path):
    def row(abbr: str, dom: str, cha: str, rev: str = "") -> list[str]:
        return ["상호", "", abbr, dom, "", "", "", "", "", "", cha, rev, "", ""]

    _fake(tmp_path, "20251101", [
        row("CONM", "명V100", "2차(2020-12)(폐기)"),
        row("CONM", "명V200", "5차(2022-07) (폐기후제정)"),
        row("OLD", "명V100", "3차(2021-10)", "폐기"),
    ])
    std = load_standards(tmp_path)
    assert std.terms["상호"].domain == "명V200"
    assert resolve_attribute(std, "상호").data_type == "varchar(200)"


def test_loader_picks_latest_complete_version(tmp_path):
    _fake(tmp_path, "20241101", [])
    _fake(tmp_path, "20251101", [])
    (tmp_path / "공통표준용어_20261101.csv").write_text("x", encoding="utf-8")  # 3종이 다 없는 판
    assert load_standards(tmp_path).version == "20251101"
    assert load_standards(tmp_path, "20241101").version == "20241101"
    with pytest.raises(FileNotFoundError):
        load_standards(tmp_path, "20261101")


# ── 표준 검증 ──────────────────────────────────────────


def test_normalize_pg_type():
    from nlxpg.standards.check import normalize_pg_type

    assert normalize_pg_type("character varying(100)") == "varchar(100)"
    assert normalize_pg_type("character(10)") == "char(10)"
    assert normalize_pg_type("numeric(15,0)") == "numeric(15)"
    assert normalize_pg_type("timestamp without time zone") == "timestamp"


def test_check_columns(std):
    from nlxpg.standards.check import ColumnInput, check_columns

    cols = [
        ColumnInput("t", "cnpt_nm", "character varying(100)", "거래처명"),  # 표준
        ColumnInput("t", "reg_ymd", "character(8)", "등록일자"),            # 원형 타입도 표준
        ColumnInput("t", "reg_ymd", "date", None),                          # 영문명으로 역조회
        ColumnInput("t", "cnpt_nm", "varchar(50)", "거래처명"),             # 길이 불일치
        ColumnInput("t", "cust_name", "varchar(100)", "거래처명"),          # 영문명 불일치
        ColumnInput("t", "gds_cd", "char(5)", "상품코드"),                  # 조합
        ColumnInput("t", "foo", "text", None),                              # 판정 불가
    ]
    r = check_columns(std, cols)
    assert [c.verdict for c in r.columns] == [
        "standard", "standard", "standard", "type_mismatch", "name_mismatch", "composed", "unknown",
    ]
    assert r.columns[2].matched_by == "physical" and r.columns[2].std_term == "등록일자"
    assert any("원형" in n for n in r.columns[1].notes)
    assert r.columns[4].std_physical == "cnpt_nm"
    assert r.compliance == round(3 / 7, 4)


def test_logical_from_comment():
    from nlxpg.standards.check import logical_from_comment

    assert logical_from_comment("거래처명: 상품을 구매하는 법인") == "거래처명"
    assert logical_from_comment("주문일자") == "주문일자"
    assert logical_from_comment(None) is None


# ── 영문 컬럼명 추정 ────────────────────────────────────


@pytest.mark.parametrize(("column", "words"), [
    ("customer_id", ["고객", "아이디"]),
    ("create_date", ["생성", "일자"]),      # create → Creation(활용형), date → 일자(관용어)
    ("postal_code", ["우편번호"]),          # 두 토큰이 한 단어
    ("lastUpdate", ["최종", "갱신"]),       # camelCase
    ("address2", ["주소"]),                 # 숫자는 버린다
    ("special_features", None),            # 모르는 단어가 있으면 추정하지 않는다
])
def test_infer_words(std, column, words):
    from nlxpg.standards.english import infer_words

    r = infer_words(std, column)
    assert (r[0] if r else None) == words


def test_check_columns_english_only_schema(std):
    from nlxpg.standards.check import ColumnInput, check_columns

    r = check_columns(std, [
        ColumnInput("address", "phone", "varchar(20)"),        # 전화번호 → 표준 telno
        ColumnInput("payment", "amount", "numeric(5,2)"),
        ColumnInput("film", "special_features", "text[]"),
    ])
    phone, amount, special = r.columns
    assert (phone.verdict, phone.matched_by, phone.std_physical) == ("name_mismatch", "english", "telno")
    assert any(n.startswith("영문명으로 추정") for n in phone.notes)
    assert amount.matched_by == "english" and amount.std_term
    assert special.verdict == "unknown"
