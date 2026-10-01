"""5단계: 모델링 — 명명, 타입, PK·FK, N:M 연결 테이블."""
from nlxpg.modeling.design import design_schema
from nlxpg.modeling.naming import to_identifier
from nlxpg.modeling.types import normalize_type

__all__ = ["design_schema", "normalize_type", "to_identifier"]
