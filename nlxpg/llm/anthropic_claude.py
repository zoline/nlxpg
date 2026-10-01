"""Claude (Anthropic API) 클라이언트 — 외부 API (ADR-0008).

사내 문서가 아닌 실행(공개·테스트 문서)에만 쓴다. 이 제약은 호출하는 쪽(api/app.py)에서 지킨다.

구조화 출력은 output_config.format(json_schema)으로 강제한다. Claude는 모든 객체에
additionalProperties=false를 요구하고 숫자·길이 제약을 받지 않으므로 보내기 전에 스키마를 고친다.
빠진 제약은 complete_json이 pydantic으로 다시 검증한다.
"""
from __future__ import annotations

from typing import Any

import anthropic

from nlxpg.llm.base import ChatResult, LLMError, StructuredLLM

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-opus-5-5"

# 안전 분류기가 거절하면 같은 요청을 다른 모델로 다시 돌린다(서버 측 fallback).
# 이 모델들에서만 받는 매개변수다.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_DROP_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
              "minLength", "maxLength", "pattern", "maxItems", "uniqueItems", "minProperties",
              "maxProperties"}


def api_schema(schema: Any) -> Any:
    """pydantic JSON Schema → Claude 구조화 출력이 받는 형태."""
    if isinstance(schema, list):
        return [api_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: api_schema(v) for k, v in schema.items() if k not in _DROP_KEYS}
    if out.get("minItems", 0) > 1:
        out.pop("minItems")
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out


class ClaudeLLM(StructuredLLM):
    provider = "anthropic"

    def __init__(
        self,
        model: str,
        base_url: str | None,
        api_key: str,
        *,
        max_tokens: int = 16000,
        timeout: float = 300.0,
        low_effort: bool = False,
    ) -> None:
        super().__init__("json_schema")
        if not api_key:
            raise ValueError("Anthropic API 키가 없다 — 모델 설정에서 프로필에 키를 넣는다")
        self.model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url or DEFAULT_BASE_URL, timeout=timeout)
        self._max_tokens = max_tokens
        # Opus 5.5 등은 생각(thinking)을 끌 수 없고 temperature도 받지 않는다.
        # 역할의 "생각 끄기"는 effort=low로 대신한다. 아니면 high(Opus 5.5 기본값 medium보다 한 단계 위).
        self._effort = "low" if low_effort else "high"

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
    ) -> ChatResult:
        output_config: dict[str, Any] = {"effort": self._effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": api_schema(json_schema)}
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": self._max_tokens, "system": system_prompt,
            "messages": messages, "output_config": output_config,
        }
        # 긴 문서·큰 max_tokens에서 HTTP 시간 초과를 피하려고 스트리밍으로 받는다.
        if self.model in _FALLBACK_MODELS:
            stream = self._client.beta.messages.stream(
                **kwargs, betas=[_FALLBACK_BETA], fallbacks="default")
        else:
            stream = self._client.messages.stream(**kwargs)
        try:
            async with stream as s:
                msg = await s.get_final_message()
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic API 접속 실패: {exc}") from exc

        if msg.stop_reason == "refusal":
            detail = getattr(msg, "stop_details", None)
            raise LLMError(f"Claude가 요청을 거절했다: {getattr(detail, 'explanation', None) or ''}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        finish = "length" if msg.stop_reason == "max_tokens" else msg.stop_reason
        usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
        return ChatResult(text, finish, usage)

    async def aclose(self) -> None:
        await self._client.close()
