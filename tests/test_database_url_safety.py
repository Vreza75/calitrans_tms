"""Regression coverage for the P0 fix: a FastAPI/worker process silently
switching its database connection away from an explicitly-configured
DATABASE_URL after a lazy import (e.g. services.operations_inbox_service,
imported lazily from inside application/work_items command functions)
made Streamlit importable in-process.

Root cause (confirmed directly during PR #12/#22/#23 integration
verification, not theoretical): config.get_secret()'s general precedence
checked Streamlit secrets before the environment variable a caller
explicitly set. get_streamlit_secret() only returns a value once
"streamlit" appears in sys.modules - which only happens the instant
something imports it, however transitively. A long-lived process that
started with DATABASE_URL correctly pointed at a disposable database
could, mid-request, silently start resolving to whatever
.streamlit/secrets.toml holds (a real, production-adjacent URL in this
repository) the first time any request path touched Operations Inbox
legacy services - with zero code change and zero warning.

These tests cover: the reordered DATABASE_URL-specific precedence in
config.get_database_url()/get_secret() (env wins, Streamlit secrets are
now the fallback, not the override), immunity to a lazy streamlit import
happening after the environment variable is already set, the legacy
Streamlit-Cloud fallback path still working when no environment variable
exists at all, db_client.py's independent fail-closed QA host-allowlist
guard, and that engine caching cannot silently swap databases out from
under a caller.

Safety: every test here uses fabricated URLs (example.invalid,
127.0.0.1) and monkeypatched functions only - none of them open a real
network connection, and SQLAlchemy's create_engine() does not connect
eagerly (only on first actual query), so even the tests that call
db_client.get_engine() directly never touch a socket.
"""
from __future__ import annotations

import pytest

import config
import db_client

DISPOSABLE_URL = "postgresql://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa"
DISPOSABLE_IDENTITY = "127.0.0.1:5433/calitrans_qa"
FAKE_REAL_SECRET_URL = "postgresql://prod_user:prod_pw@db.example.invalid:5432/production"


def _isolate_all_sources(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: None)
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: None)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: None)


# --- Requirement 2/3: DATABASE_URL precedence is reordered, env wins ----


def test_database_url_env_var_wins_over_streamlit_secret(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)

    assert config.get_secret("DATABASE_URL") == DISPOSABLE_URL
    assert config.get_config_source("DATABASE_URL") == "environment/.env"


def test_database_url_env_var_wins_over_local_secrets_toml_file(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)

    assert config.get_secret("DATABASE_URL") == DISPOSABLE_URL


def test_database_url_local_env_file_wins_over_streamlit_secret(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: DISPOSABLE_URL)

    assert config.get_secret("DATABASE_URL") == DISPOSABLE_URL
    assert config.get_config_source("DATABASE_URL") == "local .env file"


def test_other_secrets_are_unaffected_streamlit_still_wins(monkeypatch):
    """The reorder is scoped to DATABASE_URL only - every other secret
    keeps get_secret()'s original, unmodified precedence."""
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: "from-streamlit")
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: None)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: None)
    monkeypatch.setenv("SOME_OTHER_SECRET", "from-env")

    assert config.get_secret("SOME_OTHER_SECRET") == "from-streamlit"


# --- Requirement 4: legacy Streamlit Cloud fallback still works --------


def test_database_url_falls_back_to_streamlit_secret_when_env_absent(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: DISPOSABLE_URL)

    assert config.get_secret("DATABASE_URL") == DISPOSABLE_URL
    assert config.get_config_source("DATABASE_URL") == "streamlit secrets"


def test_database_url_falls_back_to_local_secrets_toml_as_last_resort(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: DISPOSABLE_URL)

    assert config.get_secret("DATABASE_URL") == DISPOSABLE_URL
    assert config.get_config_source("DATABASE_URL") == "local .streamlit/secrets.toml"


def test_database_url_missing_everywhere_returns_default(monkeypatch):
    _isolate_all_sources(monkeypatch)

    assert config.get_secret("DATABASE_URL") is None
    assert config.get_config_source("DATABASE_URL") == "missing"


# --- Requirement 6: immune to a lazy streamlit import mid-process ------


def test_database_url_does_not_change_when_streamlit_becomes_importable(monkeypatch):
    """Simulates the exact incident: DATABASE_URL set correctly, then
    something imports streamlit later in the same process (without
    this test literally importing it - monkeypatching get_streamlit_secret
    reproduces the same observable effect get_streamlit_secret() has once
    'streamlit' lands in sys.modules, without this test needing the real
    package installed as a dependency of the fix itself)."""
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)

    before = config.get_secret("DATABASE_URL")
    assert before == DISPOSABLE_URL

    # "Lazy import happens" - streamlit secrets now resolve to a real value.
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)

    after = config.get_secret("DATABASE_URL")
    assert after == before == DISPOSABLE_URL


# --- Requirement 7: engine caching cannot silently select another DB ---


def test_get_engine_returns_the_same_object_for_the_same_url():
    engine_a = db_client.get_engine(DISPOSABLE_URL)
    engine_b = db_client.get_engine(DISPOSABLE_URL)
    assert engine_a is engine_b


def test_get_engine_default_path_is_immune_to_late_streamlit_import(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, DISPOSABLE_IDENTITY)
    db_client._ENGINE_CACHE.pop(DISPOSABLE_URL, None)
    db_client._ENGINE_CACHE.pop(FAKE_REAL_SECRET_URL, None)

    engine_before = db_client.get_engine()  # no explicit url - exercises the real auto-resolve path
    assert DISPOSABLE_IDENTITY in db_client.database_identity(DISPOSABLE_URL)

    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)

    engine_after = db_client.get_engine()
    assert engine_after is engine_before
    assert FAKE_REAL_SECRET_URL not in db_client._ENGINE_CACHE


# --- Requirement 5/9: fail-closed QA host allowlist guard --------------


def test_qa_guard_is_inactive_without_the_allow_env_var(monkeypatch):
    monkeypatch.delenv(db_client._QA_ALLOW_ENV_VAR, raising=False)
    # Must not raise - existing production/dev behavior is unchanged when
    # the caller never opted into QA mode.
    db_client._enforce_qa_database_safety(FAKE_REAL_SECRET_URL)


def test_qa_guard_blocks_non_allowlisted_production_shaped_name(monkeypatch):
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "db.example.invalid:5432/production")

    with pytest.raises(RuntimeError, match="generic/production-shaped name"):
        db_client._enforce_qa_database_safety(FAKE_REAL_SECRET_URL)


def test_qa_guard_blocks_host_without_a_safe_name_marker(monkeypatch):
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "127.0.0.1:5433/calitrans")

    with pytest.raises(RuntimeError, match="no dev/test/qa/sandbox/scratch/disposable marker"):
        db_client._enforce_qa_database_safety("postgresql://u:p@127.0.0.1:5433/calitrans")


def test_qa_guard_requires_identity_env_var_even_with_a_safe_name(monkeypatch):
    """A 'qa'-shaped name alone is not enough - the operator must also
    positively acknowledge the exact target identity, same two-factor
    requirement as scripts/sample_data.py's assert_safe_target()."""
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.delenv(db_client._QA_IDENTITY_ENV_VAR, raising=False)

    with pytest.raises(RuntimeError, match=db_client._QA_IDENTITY_ENV_VAR):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_rejects_identity_mismatch(monkeypatch):
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "127.0.0.1:9999/calitrans_qa")

    with pytest.raises(RuntimeError, match="does not match"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_allows_the_exact_acknowledged_disposable_target(monkeypatch):
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, DISPOSABLE_IDENTITY)

    db_client._enforce_qa_database_safety(DISPOSABLE_URL)  # must not raise


def test_qa_guard_wired_into_get_engine(monkeypatch):
    """The guard is not just a standalone function - get_engine() itself
    calls it, for both the explicit-url and auto-resolved paths."""
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "db.example.invalid:5432/production")

    with pytest.raises(RuntimeError, match="Refusing to connect"):
        db_client.get_engine(FAKE_REAL_SECRET_URL)


# --- Requirement 10: never expose credentials or full URLs -------------


def test_qa_guard_error_messages_never_contain_the_password(monkeypatch):
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "db.example.invalid:5432/production")

    with pytest.raises(RuntimeError) as exc_info:
        db_client._enforce_qa_database_safety(FAKE_REAL_SECRET_URL)

    message = str(exc_info.value)
    assert "prod_pw" not in message
    assert "prod_user" not in message
    assert FAKE_REAL_SECRET_URL not in message


def test_database_identity_never_includes_credentials():
    identity = db_client.database_identity(FAKE_REAL_SECRET_URL)
    assert identity == "db.example.invalid:5432/production"
    assert "prod_pw" not in identity
    assert "prod_user" not in identity
