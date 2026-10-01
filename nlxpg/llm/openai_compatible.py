"""OpenAI 호환 서버(vLLM, Ollama) 클라이언트.

vLLM은 response_format={"type": "json_schema"}를 guided decoding으로 처리해 출력이
스키마를 벗어나지 못하게 한다(ADR-0002).
"""
from __future__ import annotations

from typing import Any

import httpx
import openai

from nlxpg.llm.base import ChatResult, GuidedMode, StructuredLLM


class OpenAICompatibleLLM(StructuredLLM):
    provider = "openai"

    def __init__(
        self,
        model: str,
        base_url: str | None,
        api_key: str,
        *,
        guided_mode: GuidedMode = "json_schema",
        temperature: float = 0.0,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        verify_ssl: bool = True,
        disable_thinking: bool = False,
    ) -> None:
        super().__init__(guided_mode)
        self.model = model
        # 인증 없는 자체 호스팅 서버라도 openai SDK는 빈 키를 거부한다 — pgxnl과 같은 처리.
        http_client = httpx.AsyncClient(verify=False, timeout=timeout) if not verify_ssl else None
        self._client = openai.AsyncOpenAI(
            base_url=base_url, api_key=api_key or "not-needed", http_client=http_client,
            timeout=timeout,
        )
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Qwen3 하이브리드 reasoning은 사고 과정에 max_tokens를 다 쓸 수 있다(pgxnl 실측).
        # Qwen이 아닌 서버는 이 필드를 무시한다.
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}} if disable_thinking else None
        )

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
    ) -> ChatResult:
        response_format: Any = openai.NOT_GIVEN
        if json_schema is not None and self.guided_mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema},
            }
        elif json_schema is not None and self.guided_mode == "json_object":
            response_format = {"type": "json_object"}

        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system_prompt}, *messages],  # type: ignore[list-item]
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            response_format=response_format,
            extra_body=self._extra_body,
        )
        choice = resp.choices[0]
        usage = (
            {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
            if resp.usage else None
        )
        return ChatResult(choice.message.content or "", choice.finish_reason, usage)

    async def aclose(self) -> None:
        await self._client.close()
