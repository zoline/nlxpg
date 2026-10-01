"""테스트는 실제 .env(pgxnl 폴백 포함)에 의존하지 않도록 LLM을 가짜로 대체한다."""
from __future__ import annotations

import json
from typing import Any

import pytest

from nlxpg.ir import Attribute, Entity, Relationship, SchemaIR
from nlxpg.llm.base import ChatResult, StructuredLLM


class FakeLLM(StructuredLLM):
    provider = "fake"
    model = "fake"

    def __init__(self, replies: list[str], guided_mode: str = "json_schema") -> None:
        super().__init__(guided_mode)  # type: ignore[arg-type]
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, system_prompt, messages, *, json_schema=None, schema_name="output"):
        self.calls.append({"system": system_prompt, "messages": list(messages)})
        return ChatResult(self.replies.pop(0), "stop")


@pytest.fixture
def fake_llm():
    return FakeLLM


def extraction_reply(**kw: Any) -> str:
    return json.dumps({"entities": kw.get("entities", []),
                       "relationships": kw.get("relationships", [])}, ensure_ascii=False)


@pytest.fixture
def order_ir() -> SchemaIR:
    return SchemaIR(
        entities=[
            Entity(logical_name="거래처", physical_name="Customer", attributes=[
                Attribute(logical_name="거래처명", physical_name="customerName",
                          data_type="varchar(100)", nullable=False),
            ]),
            Entity(logical_name="주문", physical_name="order", attributes=[
                Attribute(logical_name="주문일자", physical_name="order_date",
                          data_type="", value_examples=["2026-09-30"]),
            ]),
            Entity(logical_name="상품", physical_name="product", attributes=[
                Attribute(logical_name="상품코드", physical_name="product_code",
                          value_examples=["P0001"], is_unique=True),
            ]),
            Entity(logical_name="카테고리", physical_name="category"),
        ],
        relationships=[
            Relationship(name="places", from_entity="Customer", to_entity="order", cardinality="1:N"),
            Relationship(name="classified", from_entity="product", to_entity="category",
                         cardinality="N:M"),
        ],
    )
