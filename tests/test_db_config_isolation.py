"""PR #19 Stage 1 - adversarial database-configuration isolation under pytest.

conftest.py must make it impossible for a test to silently resolve the real,
production DATABASE_URL by ANY path: inherited from the shell, injected from
.env via dotenv at config import time, read from .streamlit/secrets.toml, or
reintroduced by a late dotenv reload / import-order variation. Each test below
drives one of those leak vectors and asserts isolation holds.

These exercise conftest._install_database_isolation() directly so the guarantee
is tested, not merely assumed from the ambient session state."""
from __future__ import annotations

import importlib
import sys
import types

import pytest

import conftest

_FAKE_PROD_URL = "postgresql://postgres.PRODUCTION:secret@aws-1.pooler.supabase.com:6543/postgres"


def test_file_backed_secret_fallbacks_are_neutralized():
    import config

    # All three file-backed fallbacks return None for EVERY key, regardless of
    # what .env / .streamlit/secrets.toml actually contain on disk.
    assert config.get_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_env_secret("DATABASE_URL") is None
    assert config._read_local_env_secret("OPENAI_API_KEY") is None
    assert config._read_local_streamlit_secret("OPENAI_API_KEY") is None


def test_default_session_has_no_database_url():
    import config

    # The common case (plain `pytest`, nothing exported): DATABASE_URL is simply
    # unresolved, so any unmocked db_client call raises instead of hitting prod.
    assert config.get_secret("DATABASE_URL") is None
    assert config.get_config_source("DATABASE_URL") == "missing"


def test_inherited_shell_database_url_is_stripped(monkeypatch):
    import config

    # Adversary: a production DATABASE_URL is already in the environment (e.g. a
    # developer exported it). Re-running isolation must strip it - pytest never
    # silently uses an inherited application DATABASE_URL.
    monkeypatch.setenv("DATABASE_URL", _FAKE_PROD_URL)
    conftest._install_database_isolation()
    assert "DATABASE_URL" not in __import__("os").environ
    assert config.get_secret("DATABASE_URL") is None


def test_late_environment_injection_is_caught_by_per_test_isolation(monkeypatch):
    import config

    # Adversary: something sets DATABASE_URL mid-session. The autouse per-test
    # fixture re-runs isolation at the start of each test; simulate that re-run
    # and confirm the late value is cleared.
    monkeypatch.setenv("DATABASE_URL", _FAKE_PROD_URL)
    assert config.get_secret("DATABASE_URL") == _FAKE_PROD_URL  # present until re-isolated
    conftest._install_database_isolation()
    assert config.get_secret("DATABASE_URL") is None


def test_dotenv_reload_cannot_reinject_database_url():
    import config

    # Adversary: code re-invokes config's dotenv/.env loaders to reload secrets.
    # Both are neutralized, so neither re-injects DATABASE_URL.
    assert config.load_dotenv() is False
    assert config._load_local_env_file() is None
    assert config.get_secret("DATABASE_URL") is None


def test_streamlit_secrets_fallback_is_blocked(monkeypatch):
    import config

    # Adversary: a streamlit module is live in-process with a production
    # DATABASE_URL in st.secrets. The neutralized get_streamlit_secret ignores
    # it, so get_secret still resolves to None rather than the prod URL.
    fake_streamlit = types.ModuleType("streamlit")
    fake_streamlit.secrets = {"DATABASE_URL": _FAKE_PROD_URL}
    monkeypatch.setitem(sys.modules, "streamlit", fake_streamlit)
    assert config.get_streamlit_secret("DATABASE_URL") is None
    assert config.get_secret("DATABASE_URL") is None


def test_import_order_variation_reimport_stays_isolated(monkeypatch):
    import os

    import config

    # Adversary: a test reloads config, re-running its real import-time .env
    # side effects (which can repopulate os.environ) and restoring the real
    # loaders. The autouse fixture re-installs isolation before the NEXT test,
    # but prove it here: after a reload, re-running isolation re-strips and
    # re-neutralizes everything.
    importlib.reload(config)
    conftest._install_database_isolation()
    assert "DATABASE_URL" not in os.environ
    assert config.get_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_env_secret("DATABASE_URL") is None
    assert config.get_secret("DATABASE_URL") is None


def test_only_explicit_disposable_env_var_is_honored(monkeypatch):
    import config

    # A caller that explicitly sets a DISPOSABLE DATABASE_URL in the environment
    # (not inherited silently, not from a file) is honored, and ONLY that value.
    monkeypatch.setenv("DATABASE_URL", "postgresql://disposable-test-host/db")
    assert config.get_secret("DATABASE_URL") == "postgresql://disposable-test-host/db"
    assert config.get_config_source("DATABASE_URL") == "environment/.env"
