"""공공데이터 공통표준(용어·단어·도메인) 적용 (ADR-0005)."""
from nlxpg.standards.loader import Domain, Standards, Term, Word, load_standards
from nlxpg.standards.resolve import Resolution, resolve_attribute, resolve_entity

__all__ = [
    "Domain", "Resolution", "Standards", "Term", "Word",
    "load_standards", "resolve_attribute", "resolve_entity",
]
