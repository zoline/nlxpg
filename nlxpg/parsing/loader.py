"""문서 로더. 모든 형식을 마크다운 텍스트로 정규화한다.

표·제목 구조가 엔티티 식별의 핵심 단서라, 평문보다 마크다운(제목 #, 표 |)으로 받는다.
파싱 도구는 아직 선정 전이다(plan.md §핵심 기술 요소). 지금은 Docling이 설치되어 있으면
PDF/DOCX에 쓰고, HWP/HWPX는 방식이 확정될 때까지(ADR 대기) 명시적으로 거부한다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".htm", ".png", ".jpg", ".jpeg", ".tiff"}
_HWP_SUFFIXES = {".hwp", ".hwpx"}


@dataclass
class Document:
    doc_id: str
    title: str
    text: str  # 마크다운
    source_path: str | None = None
    media_type: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_document(path: str | Path, *, doc_id: str | None = None, title: str | None = None) -> Document:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text = p.read_text(encoding="utf-8")
    elif suffix in _DOCLING_SUFFIXES:
        text = _load_with_docling(p)
    elif suffix in _HWP_SUFFIXES:
        raise NotImplementedError(
            "HWP/HWPX 파싱 방식이 아직 정해지지 않았다(docs/adr/README.md 결정 대기). "
            "당분간 PDF나 DOCX로 변환해 입력한다."
        )
    else:
        raise ValueError(f"지원하지 않는 문서 형식: {suffix}")

    return Document(
        doc_id=doc_id or p.stem,
        title=title or _first_heading(text) or p.stem,
        text=text,
        source_path=str(p),
        media_type=suffix.lstrip("."),
    )


def _load_with_docling(p: Path) -> str:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            f"{p.suffix} 파싱에는 docling이 필요하다: pip install -e '.[parsing]'"
        ) from exc
    return DocumentConverter().convert(str(p)).document.export_to_markdown()


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip() or None
    return None
