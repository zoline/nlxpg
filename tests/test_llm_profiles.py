import pytest

from nlxpg.crypto import decrypt, encrypt, new_secret_key
from nlxpg.llm.factory import guided_mode
from nlxpg.llm.profiles import to_settings
from nlxpg.settings import Settings


def test_encrypt_roundtrip_and_wrong_key():
    key = new_secret_key()
    token = encrypt("sk-비밀", key)
    assert "비밀" not in token and decrypt(token, key) == "sk-비밀"
    with pytest.raises(ValueError, match="NLXPG_SECRET_KEY"):
        decrypt(token, new_secret_key())


def _row(**kw):
    base = {"provider": "openai", "model": "qwen3.6-35b", "base_url": "https://h:8000/v1", "temperature": 0.0,
            "max_tokens": 4096, "verify_ssl": False, "guided_mode": None, "api_key_enc": None,
            "watsonx_password_enc": None}
    return base | kw


def test_to_settings_openai_profile_ignores_env_llm(monkeypatch):
    monkeypatch.setenv("NLXPG_LLM_PROVIDER", "watsonx")  # .env의 LLM 설정은 끼어들지 않는다
    key = new_secret_key()
    s = to_settings(_row(api_key_enc=encrypt("k1", key)), Settings(), key, disable_thinking=True)
    assert (s.llm_provider, s.llm_model, s.llm_base_url) == ("openai", "qwen3.6-35b", "https://h:8000/v1")
    assert s.openai_api_key == "k1" and s.llm_verify_ssl is False and s.llm_disable_thinking is True
    assert guided_mode(s) == "json_schema"  # vLLM 기본은 guided decoding


def test_to_settings_watsonx_profile():
    key = new_secret_key()
    row = _row(provider="watsonx", model="openai/gpt-oss-120b", base_url="https://cpd/",
               watsonx_project_id="p1", watsonx_space_id=None, watsonx_username="u", watsonx_instance_id=None,
               watsonx_api_version="2024-05-01", watsonx_password_enc=encrypt("pw", key))
    s = to_settings(row, Settings(), key)
    assert (s.watsonx_project_id, s.watsonx_username, s.watsonx_password) == ("p1", "u", "pw")
    assert s.watsonx_verify_ssl is False and guided_mode(s) == "json_object"


def test_anthropic_profile_builds_claude_client():
    from nlxpg.llm.anthropic_claude import ClaudeLLM
    from nlxpg.llm.factory import create_llm

    key = new_secret_key()
    row = _row(provider="anthropic", model="claude-opus-5-5", base_url="https://api.anthropic.com",
               verify_ssl=True, api_key_enc=encrypt("sk-ant-x", key))
    s = to_settings(row, Settings(), key, disable_thinking=True)
    assert guided_mode(s) == "json_schema"
    llm = create_llm(s)
    assert isinstance(llm, ClaudeLLM) and llm.model == "claude-opus-5-5" and llm._effort == "low"


def test_anthropic_profile_without_key_is_rejected():
    from nlxpg.llm.factory import create_llm

    s = to_settings(_row(provider="anthropic", model="claude-opus-5-5"), Settings(), new_secret_key())
    with pytest.raises(ValueError, match="API 키"):
        create_llm(s)


def test_claude_api_schema_is_strict():
    from pydantic import BaseModel, Field

    from nlxpg.llm.anthropic_claude import api_schema

    class Item(BaseModel):
        name: str = Field(min_length=1, max_length=63)
        score: int = Field(ge=1, le=5)

    class Out(BaseModel):
        items: list[Item] = Field(min_length=2)

    sch = api_schema(Out.model_json_schema())
    item = sch["$defs"]["Item"]
    assert sch["additionalProperties"] is False and item["additionalProperties"] is False
    assert "minLength" not in item["properties"]["name"] and "maximum" not in item["properties"]["score"]
    assert "minItems" not in sch["properties"]["items"]
