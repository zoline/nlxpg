import pytest
from pydantic import BaseModel

from nlxpg.llm.base import LLMError, call_label
from tests.conftest import FakeLLM


class Out(BaseModel):
    answer: str


async def test_recorder_logs_each_attempt_with_label():
    llm = FakeLLM(['{"wrong": 1}', '{"answer": "ok"}'])
    recs = []

    async def rec(r):
        recs.append(r)

    llm.recorder = rec
    token = call_label.set("문서 › 1장")
    try:
        assert (await llm.complete_json("sys", "user", Out)).answer == "ok"
    finally:
        call_label.reset(token)
    assert [r["attempt"] for r in recs] == [0, 1]
    assert recs[0]["error"].startswith("검증 실패") and "error" not in recs[1]
    assert recs[0]["label"] == "문서 › 1장" and recs[1]["response"] == '{"answer": "ok"}'
    assert len(recs[1]["messages"]) == 3  # 수정 요청은 이전 응답과 오류를 함께 보낸다
    assert recs[0]["duration_ms"] >= 0


async def test_recorder_failure_does_not_break_call_and_chat_error_is_logged():
    llm = FakeLLM(['{"answer": "ok"}'])

    async def broken(r):
        raise RuntimeError("db down")

    llm.recorder = broken
    assert (await llm.complete_json("s", "u", Out)).answer == "ok"

    recs = []

    async def rec(r):
        recs.append(r)

    llm2 = FakeLLM([])  # 응답이 없으면 chat이 IndexError
    llm2.recorder = rec
    with pytest.raises(IndexError):
        await llm2.complete_json("s", "u", Out)
    assert recs[0]["error"].startswith("IndexError")

    llm3 = FakeLLM(["x", "y"])
    llm3.recorder = rec
    with pytest.raises(LLMError):
        await llm3.complete_json("s", "u", Out)
