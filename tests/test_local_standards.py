"""기관 추가 표준 (ADR-0009). 실제 공통표준 CSV 위에 덧붙인다."""
from pathlib import Path

import pytest

from nlxpg.standards import load_standards, resolve_attribute, resolve_entity
from nlxpg.standards.check import ColumnInput, check_columns
from nlxpg.standards.local import (
    LocalConflict,
    LocalItem,
    apply_local,
    check_item,
    from_csv,
    to_csv,
)

STD_DIR = Path(__file__).parent.parent / "data" / "standards"


@pytest.fixture(scope="module")
def base():
    return load_standards(STD_DIR)


def test_alias_makes_nonstandard_name_local(base):
    assert resolve_attribute(base, "비행기번호").status == "nonstandard"
    std = apply_local(base, [LocalItem("alias", "비행기", target="항공기", item_id=1)]).standards

    r = resolve_attribute(std, "비행기번호")
    assert (r.status, r.physical_name, r.logical_name) == ("local", "arpln_no", "항공기번호")
    assert "기관 이음동의어 '비행기' → '항공기'" in r.notes
    e = resolve_entity(std, "비행기")
    assert (e.status, e.physical_name) == ("local", "arpln")
    # 원본은 그대로다
    assert "비행기" not in base.word_alias and base.local_items == 0
    assert std.local_items == 1 and std.local_snapshot == [{"kind": "alias", "name": "비행기", "target": "항공기"}]


def test_local_word_and_term(base):
    items = [LocalItem("word", "쀍", abbr="qqq", english="Qqq", item_id=1),
             LocalItem("term", "쀍번호", domain="번호V20", item_id=2)]
    applied = apply_local(base, items)
    assert not applied.rejected
    std = applied.standards
    assert applied.applied[1].abbr == "QQQ_NO"  # 용어 약어는 단어 약어를 잇는다
    r = resolve_attribute(std, "쀍번호")
    assert (r.status, r.physical_name, r.domain, r.data_type) == ("local", "qqq_no", "번호V20", "varchar(20)")
    assert "기관 용어 '쀍번호'" in r.notes
    # 기관 단어 조합(용어 아님)도 local
    assert resolve_attribute(std, "쀍명").status == "local"


@pytest.mark.parametrize(("item", "msg"), [
    (LocalItem("alias", "회원", target="항공기"), "이미 공통표준 단어"),
    (LocalItem("alias", "쀍", target="없는단어"), "대표단어"),
    (LocalItem("word", "쀍", abbr="MBR"), "이미 단어 '회원'"),
    (LocalItem("word", "쀍", abbr="1AB"), "영문 대문자로 시작"),
    (LocalItem("word", "쀍", abbr="QQQ", is_format=True, domain_class="없는분류"), "도메인 분류"),
    (LocalItem("term", "거래처명", domain="명V100"), "이미 공통표준 용어"),
    (LocalItem("term", "쀍번호", domain="번호V20"), "단어로 나눌 수 없다"),
    (LocalItem("term", "회원비고", domain="없는도메인"), "공통표준 도메인이 아니다"),
])
def test_conflicts_with_original_are_rejected(base, item, msg):
    with pytest.raises(LocalConflict, match=msg):
        check_item(base, item, base)


def test_item_conflicting_with_new_original_is_skipped(base):
    """공통표준 새 판에 같은 단어가 들어오면 그 기관 항목은 적용하지 않고 이유를 남긴다."""
    applied = apply_local(base, [LocalItem("word", "회원", abbr="QQQ", item_id=7)])
    assert not applied.applied and applied.rejected[0][0].item_id == 7
    assert applied.standards.words["회원"].abbr == "MBR"


def test_csv_roundtrip():
    items = [LocalItem("alias", "비행기", target="항공기"),
             LocalItem("word", "쀍", abbr="QQQ", english="Qqq", is_format=True, domain_class="번호"),
             LocalItem("term", "쀍번호", abbr="QQQ_NO", domain="번호V20")]
    text = to_csv(items)
    assert text.startswith("﻿구분,이름")
    assert from_csv(text) == items
    with pytest.raises(LocalConflict, match="구분"):
        from_csv("구분,이름\n모름,x\n")


def test_check_verdict_local(base):
    std = apply_local(base, [LocalItem("alias", "비행기", target="항공기")]).standards
    report = check_columns(std, [
        ColumnInput("t", "arpln_no", "varchar(50)", "비행기번호"),
        ColumnInput("t", "plane_no", "varchar(50)", "비행기번호"),
    ])
    assert [c.verdict for c in report.columns] == ["local", "name_mismatch"]
    assert report.summary["local"] == 1 and report.compliance == 0.0  # 기관 표준은 준수율에 넣지 않는다
