"""스키마 중간 표현(IR).

plan.md §스키마 중간 표현의 초안을 그대로 옮겼다. 모든 단계가 이 형식을 주고받고,
PostgreSQL DDL은 마지막(generate/ddl.py)에 이 표현에서 생성한다. 추출·적재 프로젝트와의
인터페이스이기도 하므로 필드를 바꿀 때는 schema_version을 올린다.

미정(plan.md): 필드 이름과 필수 여부, 버전 관리 방식.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.2"  # 0.2: 공통표준 적용 결과 필드 (ADR-0005)

Cardinality = Literal["1:1", "1:N", "N:1", "N:M"]
#: 공통표준 적용 결과. common: 공통표준용어, composed: 표준단어 조합(DB표준 후보),
#: nonstandard: 표준단어로 분할되지 않음, None: 표준을 적용하지 않음.
StandardStatus = Literal["common", "composed", "nonstandard"]


class Evidence(BaseModel):
    doc_id: str
    span: str = Field(description="근거 위치(조항, 절 제목, 표 이름 등) 또는 짧은 인용")


class SourceDocument(BaseModel):
    doc_id: str
    title: str


class Attribute(BaseModel):
    logical_name: str = Field(description="한글 논리명")
    physical_name: str = Field(description="영문 물리명 (snake_case)")
    description: str = ""
    data_type: str = Field("text", description="PostgreSQL 타입, 예: varchar(100), integer, date")
    nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False
    value_examples: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    #: 공통표준도메인명 (예: 연월일C8). data_type은 ADR-0005에 따라 바뀐 PostgreSQL 타입이다.
    domain: str | None = None
    standard: StandardStatus | None = None
    standard_notes: list[str] = Field(default_factory=list)


class Entity(BaseModel):
    logical_name: str
    physical_name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    standard: StandardStatus | None = None
    standard_notes: list[str] = Field(default_factory=list)

    def attribute(self, physical_name: str) -> Attribute | None:
        return next((a for a in self.attributes if a.physical_name == physical_name), None)


class ForeignKey(BaseModel):
    table: str
    columns: list[str]
    #: 참조 대상 컬럼. 비우면 대상 엔티티의 PK.
    ref_columns: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    name: str
    from_entity: str
    to_entity: str
    cardinality: Cardinality = "1:N"
    description: str = ""
    foreign_key: ForeignKey | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class SchemaIR(BaseModel):
    schema_version: str = SCHEMA_VERSION
    #: 적용한 공통표준 판 (예: 20251101). None이면 적용하지 않음.
    standards_version: str | None = None
    source_documents: list[SourceDocument] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    def entity(self, physical_name: str) -> Entity | None:
        return next((e for e in self.entities if e.physical_name == physical_name), None)
