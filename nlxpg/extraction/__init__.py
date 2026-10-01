"""3단계: 섹션별 엔티티·속성·관계 구조화 추출."""
from nlxpg.extraction.extractor import extract_document, extract_section
from nlxpg.extraction.schema import ExtractionResult

__all__ = ["ExtractionResult", "extract_document", "extract_section"]
