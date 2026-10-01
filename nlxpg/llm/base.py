"""구조화 출력 LLM 공통 인터페이스.

pgxnl의 BaseLLMClient는 ReAct 도구 호출용이라 메시지 히스토리 포맷 변환이 중심이다.
nlxpg는 "문서 조각 → 정해진 JSON" 호출만 하므로 인터페이스를 complete_json 하나로 줄였다.

guided decoding(vLLM의 response_format=json_schema)을 쓰면 형식 오류가 원천적으로
사라지지만, watsonx처럼 json_object까지만 지원하는 경로도 있다. 그래서 항상 pydantic으로
다시 검증하고, 실패하면 검증 오류를 모델에 돌려주고 한 번 더 요청한다.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

GuidedMode = Literal["json_schema", "json_object", "none"]
T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: 디버그 기록에 붙일 호출 위치(예: "문서 › 섹션 경로"). 호출하는 쪽이 정한다(extraction/extractor.py).
call_label: contextvars.ContextVar[str | None] = contextvars.ContextVar("call_label", default=None)

#: 디버그 모드에서 LLM 호출 1회(시도 1회)마다 불린다. 기록 실패가 실행을 멈추지 않게 예외는 삼킨다.
Recorder = Callable[[dict[str, Any]], Awaitable[None]]


class LLMError(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


class StructuredLLM(ABC):
    provider: str
    model: str

    def __init__(self, guided_mode: GuidedMode) -> None:
        self.guided_mode = guided_mode
        #: 디버그 모드(실행 단위)에서만 설정한다 — service.RunService.execute
        self.recorder: Recorder | None = None

    async def _record(self, **rec: Any) -> None:
        if self.recorder is None:
            return
        try:
            await self.recorder({"label": call_label.get(), **rec})
        except Exception:  # 디버그 기록 때문에 설계가 실패하면 안 된다
            log.exception("LLM 호출 기록 실패")

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
    ) -> ChatResult:
        """json_schema가 주어지면 guided_mode에 따라 출력 형식을 강제한다."""

    async def aclose(self) -> None:
        pass

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        max_repairs: int = 1,
    ) -> T:
        schema = output_model.model_json_schema()
        if self.guided_mode != "json_schema":
            # 서버가 스키마를 강제하지 못하면 프롬프트로라도 알려 준다.
            system_prompt = (
                f"{system_prompt}\n\n반드시 아래 JSON Schema를 따르는 JSON 객체 하나만 출력하라. "
                f"설명이나 코드 블록 표시는 붙이지 않는다.\n{json.dumps(schema, ensure_ascii=False)}"
            )
        messages = [{"role": "user", "content": user_prompt}]

        for attempt in range(max_repairs + 1):
            started = time.monotonic()
            base = {"attempt": attempt, "schema_name": output_model.__name__,
                    "system_prompt": system_prompt, "messages": list(messages)}
            try:
                result = await self.chat(
                    system_prompt, messages, json_schema=schema, schema_name=output_model.__name__
                )
            except Exception as exc:
                await self._record(**base, duration_ms=_ms(started), error=f"{type(exc).__name__}: {exc}")
                raise
            if result.finish_reason == "length":
                log.warning("LLM 출력이 max_tokens에서 잘렸다 (attempt %d)", attempt)
            rec = {**base, "duration_ms": _ms(started), "response": result.text,
                   "finish_reason": result.finish_reason, "usage": result.usage}
            try:
                parsed = output_model.model_validate_json(_strip_fence(result.text))
            except ValidationError as exc:
                await self._record(**rec, error=f"검증 실패: {exc}")
                if attempt == max_repairs:
                    raise LLMError(f"구조화 출력 검증 실패: {exc}") from exc
                log.info("구조화 출력 검증 실패, 수정 요청 (attempt %d)", attempt)
                messages += [
                    {"role": "assistant", "content": result.text},
                    {
                        "role": "user",
                        "content": "위 출력이 JSON Schema 검증에 실패했다. 오류를 고쳐 JSON만 다시 "
                        f"출력하라.\n오류:\n{exc}",
                    },
                ]
                continue
            await self._record(**rec)
            return parsed
        raise AssertionError("unreachable")


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _strip_fence(text: str) -> str:
    return _FENCE.sub("", text.strip()).strip()
