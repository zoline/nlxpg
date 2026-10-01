from pathlib import Path

from nlxpg.parsing import load_document, split_sections

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "customer_rules.md"


def test_load_markdown():
    doc = load_document(SAMPLE)
    assert doc.doc_id == "customer_rules"
    assert doc.title == "거래처 관리 규정"


def test_sections_keep_heading_path():
    doc = load_document(SAMPLE)
    secs = split_sections(doc.doc_id, doc.text, max_chars=200)
    assert len(secs) > 1
    assert any("제3조" in s.path for s in secs)
    assert all(s.path.startswith("거래처 관리 규정") for s in secs)


def test_small_sections_are_merged():
    doc = load_document(SAMPLE)
    assert len(split_sections(doc.doc_id, doc.text, max_chars=100_000)) == 1


def test_long_paragraph_is_cut():
    secs = split_sections("d", "# t\n\n" + "가" * 500, max_chars=100)
    assert all(len(s.text) <= 100 for s in secs)
