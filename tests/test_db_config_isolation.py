"""PR #19 Stage 1 - database configuration isolation under pytest.

conftest.py neutralizes every local-secret-file fallback for the whole test
session so that no test can silently resolve the real, production DATABASE_URL
out of .env or .streamlit/secrets.toml (both hold the live URL in this repo).
These tests lock that guarantee in: the only way a test gets a DATABASE_URL is
an explicitly-exported (disposable) environment variable."""
from __future__ import annotations


def test_local_secret_file_fallbacks_are_neutralized():
    import config

    # All three file-backed fallbacks return None for EVERY key (conftest
    # replaced them session-wide), so get_secret() can only resolve from
    # os.environ - never from .env / .streamlit/secrets.toml.
    assert config.get_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_env_secret("DATABASE_URL") is None
    # A key that would only ever live in the local secret files resolves to None.
    assert config._read_local_env_secret("OPENAI_API_KEY") is None
    assert config._read_local_streamlit_secret("OPENAI_API_KEY") is None


def test_database_url_never_leaks_from_files_only_from_env(monkeypatch):
    import config

    # With nothing in the environment, DATABASE_URL must be unresolved - NOT the
    # production Supabase URL sitting in .env / secrets.toml.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.get_secret("DATABASE_URL") is None
    assert config.get_config_source("DATABASE_URL") == "missing"

    # Only an explicitly-set (disposable) env var is honored, and exactly it.
    monkeypatch.setenv("DATABASE_URL", "postgresql://disposable-test-host/db")
    assert config.get_secret("DATABASE_URL") == "postgresql://disposable-test-host/db"
    assert config.get_config_source("DATABASE_URL") == "environment/.env"
