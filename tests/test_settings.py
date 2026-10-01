from nlxpg.settings import Settings, mask_dsn, replace_dbname


def test_nlxpg_overrides_pgxnl(monkeypatch):
    monkeypatch.setenv("PGXNL_LLM_MODEL", "pgxnl-model")
    monkeypatch.setenv("NLXPG_LLM_MODEL", "nlxpg-model")
    assert Settings().llm_model == "nlxpg-model"


def test_falls_back_to_pgxnl(monkeypatch):
    monkeypatch.delenv("NLXPG_LLM_MODEL", raising=False)
    monkeypatch.setenv("PGXNL_LLM_MODEL", "pgxnl-model")
    assert Settings().llm_model == "pgxnl-model"


def test_system_db_derived_from_pgxnl_dsn(monkeypatch):
    monkeypatch.delenv("NLXPG_SYSTEM_PG_DSN", raising=False)
    monkeypatch.delenv("NLXPG_SANDBOX_PG_DSN", raising=False)
    monkeypatch.setenv("PGXNL_SYSTEM_PG_DSN", "postgresql://u:p%23w@db.local:5432/pgxnl?sslmode=require")
    s = Settings()
    assert s.system_pg_dsn == "postgresql://u:p%23w@db.local:5432/nlxpg?sslmode=require"
    assert s.sandbox_pg_dsn == s.system_pg_dsn


def test_explicit_system_dsn_wins(monkeypatch):
    monkeypatch.setenv("PGXNL_SYSTEM_PG_DSN", "postgresql://u:p@h/pgxnl")
    monkeypatch.setenv("NLXPG_SYSTEM_PG_DSN", "postgresql://u:p@h/other")
    assert Settings().system_pg_dsn == "postgresql://u:p@h/other"


def test_secrets_not_in_repr(monkeypatch):
    monkeypatch.setenv("WATSONX_PASSWORD", "hunter2")
    assert "hunter2" not in repr(Settings())


def test_dsn_helpers():
    assert replace_dbname("postgresql://u:p@h:5432/a", "b") == "postgresql://u:p@h:5432/b"
    assert mask_dsn("postgresql://u:secret@h:5432/a") == "postgresql://u:***@h:5432/a"


def test_testdoc_llm_role(monkeypatch):
    from nlxpg.llm.factory import settings_for

    monkeypatch.setenv("NLXPG_LLM_PROVIDER", "watsonx")
    monkeypatch.setenv("NLXPG_LLM_MODEL", "extract-model")
    monkeypatch.setenv("NLXPG_TESTDOC_LLM_PROVIDER", "openai_compatible")  # pgxnl 프로필 표기
    monkeypatch.setenv("NLXPG_TESTDOC_LLM_MODEL", "qwen3.6-35b")
    monkeypatch.setenv("NLXPG_TESTDOC_LLM_VERIFY_SSL", "false")
    monkeypatch.setenv("NLXPG_TESTDOC_LLM_API_KEY", "k")
    s = Settings()
    extract, testdoc = settings_for(s, "extract"), settings_for(s, "testdoc")
    assert (extract.llm_provider, extract.llm_model) == ("watsonx", "extract-model")
    assert (testdoc.llm_provider, testdoc.llm_model) == ("openai", "qwen3.6-35b")
    assert testdoc.llm_verify_ssl is False and testdoc.openai_api_key == "k"
    assert testdoc.llm_disable_thinking is True


def test_testdoc_falls_back_to_extract_llm(monkeypatch):
    from nlxpg.llm.factory import settings_for

    monkeypatch.setenv("NLXPG_TESTDOC_LLM_PROVIDER", "")
    s = Settings()
    assert settings_for(s, "testdoc").llm_model == s.llm_model


def test_openai_provider_without_base_url_refuses(monkeypatch):
    """주소가 비면 openai SDK가 공개 OpenAI API로 보낸다 — 호출하지 않고 막는다 (ADR-0002)."""
    import pytest

    from nlxpg.llm import create_llm

    monkeypatch.setenv("NLXPG_LLM_PROVIDER", "openai")
    monkeypatch.setenv("NLXPG_LLM_BASE_URL", "")
    monkeypatch.setenv("PGXNL_LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="NLXPG_LLM_BASE_URL"):
        create_llm(Settings())


def test_update_env_file_keeps_comments(tmp_path):
    from nlxpg.settings import update_env_file

    env = tmp_path / ".env"
    env.write_text("# 설명\n# NLXPG_LLM_MODEL=\nNLXPG_LLM_PROVIDER=watsonx\nKEEP=1\n", encoding="utf-8")
    update_env_file({"NLXPG_LLM_PROVIDER": "openai", "NLXPG_LLM_MODEL": "m", "NEW": "v"}, env)
    assert env.read_text(encoding="utf-8").splitlines() == [
        "# 설명", "NLXPG_LLM_MODEL=m", "NLXPG_LLM_PROVIDER=openai", "KEEP=1", "NEW=v",
    ]
    fresh = tmp_path / "new.env"
    update_env_file({"A": "1"}, fresh)
    assert oct(fresh.stat().st_mode & 0o777) == "0o600"
