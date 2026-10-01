from nlxpg.parsing import Document
from nlxpg.service import _inside, versioned_doc_id


def test_versioned_doc_id_depends_on_content():
    a = Document("게시판", "게시판", "# 게시판\n내용 A")
    b = Document("게시판", "게시판", "# 게시판\n내용 B")
    assert versioned_doc_id(a) != versioned_doc_id(b)  # 같은 이름, 다른 내용 → 덮어쓰지 않는다
    assert versioned_doc_id(a).startswith("게시판-")
    a.doc_id = versioned_doc_id(a)
    assert versioned_doc_id(a) == a.doc_id  # 두 번 붙이지 않는다


def test_inside_upload_root_only(tmp_path):
    root = tmp_path / "uploads"
    (root / "abc").mkdir(parents=True)
    assert _inside(root / "abc" / "x.md", root)
    assert not _inside(root, root)  # 업로드 루트 자체는 지우지 않는다
    assert not _inside(tmp_path / "other.md", root)
    assert not _inside(root / ".." / "escape.md", root)
