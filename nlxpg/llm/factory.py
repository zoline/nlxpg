"""Settings → LLM 클라이언트."""
from __future__ import annotations

from typing import Literal, cast

from nlxpg.llm.base import GuidedMode, StructuredLLM
from nlxpg.settings import Settings

_DEFAULT_GUIDED: dict[str, GuidedMode] = {
    "openai": "json_schema", "watsonx": "json_object", "anthropic": "json_schema"}
#: pgxnl 프로필은 OpenAI 호환 서버를 'openai_compatible'로 적는다. 같은 뜻으로 받는다.
_PROVIDER_ALIASES = {"openai_compatible": "openai", "vllm": "openai"}

Role = Literal["extract", "testdoc"]


def settings_for(s: Settings, role: Role = "extract") -> Settings:
    """역할별 LLM 설정. testdoc 역할은 NLXPG_TESTDOC_LLM_*이 있으면 그것으로 바꾼다."""
    if role == "testdoc" and s.testdoc_llm_provider:
        s = s.model_copy(update={
            "llm_provider": s.testdoc_llm_provider,
            "llm_model": s.testdoc_llm_model or s.llm_model,
            "llm_base_url": s.testdoc_llm_base_url,
            "openai_api_key": s.testdoc_llm_api_key,
            "llm_verify_ssl": s.testdoc_llm_verify_ssl,
            "llm_disable_thinking": s.testdoc_llm_disable_thinking,
            "llm_max_tokens": s.testdoc_llm_max_tokens or s.llm_max_tokens,
            "llm_guided_mode": None,
        })
    provider = _PROVIDER_ALIASES.get(s.llm_provider, s.llm_provider)
    return s if provider == s.llm_provider else s.model_copy(update={"llm_provider": provider})


def guided_mode(s: Settings) -> GuidedMode:
    s = settings_for(s)
    mode = cast(GuidedMode, s.llm_guided_mode or _DEFAULT_GUIDED.get(s.llm_provider, "none"))
    # watsonx에는 json_schema 강제가 없어 json_object로 낮춘다(WatsonxLLM과 같은 규칙).
    return "json_object" if s.llm_provider == "watsonx" and mode == "json_schema" else mode


def create_llm(s: Settings, role: Role = "extract") -> StructuredLLM:
    s = settings_for(s, role)
    guided = guided_mode(s)

    if s.llm_provider == "watsonx":
        from nlxpg.llm.watsonx import WatsonxLLM

        return WatsonxLLM(
            s.llm_model, s.llm_base_url or "https://us-south.ml.cloud.ibm.com",
            api_key=s.watsonx_api_key, project_id=s.watsonx_project_id,
            space_id=s.watsonx_space_id, username=s.watsonx_username,
            password=s.watsonx_password or None, instance_id=s.watsonx_instance_id,
            guided_mode=guided, temperature=s.llm_temperature, max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout, api_version=s.watsonx_api_version,
            verify_ssl=s.watsonx_verify_ssl,
        )
    if s.llm_provider == "openai":
        from nlxpg.llm.openai_compatible import OpenAICompatibleLLM

        if not s.llm_base_url:
            # 비워 두면 openai SDK가 공개 OpenAI API로 보낸다. 사내 문서가 외부로 나가지 않게
            # 막는다(ADR-0002 온프레미스 원칙). 설정이 덜 된 새 서버에서 실제로 그렇게 호출됐다.
            raise ValueError(
                "NLXPG_LLM_BASE_URL이 비어 있다 — OpenAI 호환 서버(vLLM 등) 주소를 적는다. "
                "외부 API로 보내지 않기 위해 기본값을 두지 않는다."
            )

        return OpenAICompatibleLLM(
            s.llm_model, s.llm_base_url, s.openai_api_key, guided_mode=guided,
            temperature=s.llm_temperature, max_tokens=s.llm_max_tokens, timeout=s.llm_timeout,
            verify_ssl=s.llm_verify_ssl, disable_thinking=s.llm_disable_thinking,
        )
    if s.llm_provider == "anthropic":
        # 외부 API. 사내 문서가 아닌 실행에만 고를 수 있게 api/app.py가 막는다(ADR-0008).
        # 키는 프로필의 api_key를 openai_api_key 칸에 담아 온다(llm/profiles.py to_settings).
        try:
            from nlxpg.llm.anthropic_claude import ClaudeLLM
        except ImportError as exc:
            raise ValueError("anthropic 패키지가 없다: pip install -e '.[claude]'") from exc

        return ClaudeLLM(
            s.llm_model, s.llm_base_url, s.openai_api_key, max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout, low_effort=s.llm_disable_thinking,
        )
    raise ValueError(
        f"지원하지 않는 llm_provider: {s.llm_provider!r} (openai | watsonx | anthropic). "
        "pgxnl 설정을 그대로 쓰기 어렵다면 NLXPG_LLM_PROVIDER로 따로 지정한다."
    )
