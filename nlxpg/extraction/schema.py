"""추출용 JSON Schema (ADR-0002의 첫 번째 핵심 산출물).

IR(nlxpg/ir.py)을 LLM에게 그대로 채우게 하지 않는다. 출력 필드가 많을수록 오픈 모델의
누락·환각이 늘어서, 추출은 "문서에 있는 것"만 묻고 PK·FK·정규화 같은 설계 결정은
모델링 단계(결정론적 코드 + 이후 LLM 보조)로 미룬다. doc_id처럼 코드가 아는 값도 묻지 않는다.

guided decoding 호환성을 위해 단순한 타입만 쓴다(Optional·Union 최소화).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _all_required(schema: dict) -> None:
    """JSON Schema에서 모든 속성을 required로 표시한다. 기본값이 있는 필드는 required에 들어가지 않아,
    guided decoding이 그 필드를 통째로 빠뜨리게 둔다(2026-10-01 실측: Qwen3가 value_examples·data_type_hint를
    한 번도 내지 않음). 스키마에서만 필수로 하고 pydantic 검증은 기본값을 허용해, 빠져도 실패하지 않게 한다."""
    schema["required"] = list(schema.get("properties", {}))


class ExtractedAttribute(BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_required)

    logical_name: str = Field(description="문서에 나온 속성 이름(한글)")
    physical_name: str = Field(description="영문 snake_case 컬럼명 제안")
    description: str = Field("", description="문서에 근거한 한 문장 설명")
    data_type_hint: str = Field(
        "", description="값 예시·형식 설명으로 추정한 PostgreSQL 타입(예: varchar(12), integer, "
        "numeric(15), date). 근거가 없으면 빈 문자열"
    )
    required: bool = Field(False, description="문서가 필수라고 명시하면 true")
    identifier: bool = Field(False, description="엔티티를 식별하는 번호·코드이면 true")
    value_examples: list[str] = Field(
        default_factory=list, description="문서에 나온 값 예시를 그대로(예: '2026-09-30', 'P0001'). 없으면 빈 목록"
    )
    evidence: str = Field("", description="근거가 된 조항·표 이름 또는 짧은 인용")


class ExtractedEntity(BaseModel):
    logical_name: str = Field(description="엔티티 이름(한글)")
    physical_name: str = Field(description="영문 snake_case 단수형 테이블명 제안")
    description: str = ""
    aliases: list[str] = Field(default_factory=list, description="문서 안의 다른 호칭")
    attributes: list[ExtractedAttribute] = Field(default_factory=list)
    evidence: str = ""


class ExtractedRelationship(BaseModel):
    name: str = Field(description="관계를 나타내는 영문 동사, 예: places")
    from_entity: str = Field(description="관계 출발 엔티티의 physical_name")
    to_entity: str = Field(description="관계 도착 엔티티의 physical_name")
    cardinality: Literal["1:1", "1:N", "N:1", "N:M"]
    description: str = ""
    evidence: str = ""


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
