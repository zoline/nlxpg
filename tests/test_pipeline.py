from pathlib import Path

import pytest
from pydantic import BaseModel

from nlxpg.llm.base import LLMError
from nlxpg.parsing import load_document
from nlxpg.pipeline import design_from_documents
from tests.conftest import extraction_reply

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "customer_rules.md"


class Out(BaseModel):
    answer: str


async def test_complete_json_repairs_once(fake_llm):
    llm = fake_llm(["not json", '{"answer": "ok"}'])
    assert (await llm.complete_json("s", "u", Out)).answer == "ok"
    assert "검증에 실패" in llm.calls[1]["messages"][-1]["content"]


async def test_complete_json_gives_up(fake_llm):
    llm = fake_llm(["x", "y"])
    with pytest.raises(LLMError):
        await llm.complete_json("s", "u", Out)


async def test_schema_in_prompt_without_guided_decoding(fake_llm):
    llm = fake_llm(['```json\n{"answer": "ok"}\n```'], guided_mode="json_object")
    assert (await llm.complete_json("s", "u", Out)).answer == "ok"
    assert "JSON Schema" in llm.calls[0]["system"]


async def test_pipeline_merges_sections(fake_llm):
    doc = load_document(SAMPLE)
    sec1 = extraction_reply(entities=[{
        "logical_name": "거래처", "physical_name": "customer", "aliases": ["고객"],
        "attributes": [{"logical_name": "거래처명", "physical_name": "customer_name",
                        "data_type_hint": "varchar(100)", "required": True}],
        "evidence": "제3조",
    }])
    sec2 = extraction_reply(
        entities=[
            {"logical_name": "고객", "physical_name": "client",
             "attributes": [{"logical_name": "등급", "physical_name": "grade"}]},
            {"logical_name": "주문", "physical_name": "order",
             "attributes": [{"logical_name": "주문일자", "physical_name": "order_date",
                             "value_examples": ["2026-09-30"]}]},
        ],
        relationships=[{"name": "places", "from_entity": "client", "to_entity": "order",
                        "cardinality": "1:N", "evidence": "제5조"}],
    )
    llm = fake_llm([sec1, sec2])
    # 섹션이 정확히 2개가 되도록 상한을 맞춘다
    text_len = len(doc.text)
    result = await design_from_documents(llm, [doc], chunk_max_chars=text_len // 2 + 50,
                                         concurrency=1)
    assert len(llm.calls) == 2
    tables = [e.physical_name for e in result.schema.entities]
    assert tables == ["customer", "order_"]  # client(고객)가 customer로 합쳐짐
    cust = result.schema.entity("customer")
    assert {a.physical_name for a in cust.attributes} == {"customer_id", "customer_name", "grade"}
    assert result.schema.relationships[0].from_entity == "customer"
    assert result.valid is None  # 샌드박스 미실행
