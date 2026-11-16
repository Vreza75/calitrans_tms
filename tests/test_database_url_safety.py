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
    # Loopback host (passes the P1-2 host allowlist) with a
    # production-shaped database name - isolates the pre-existing
    # name-rejection check from the newer host-allowlist check, which
    # now runs first for a remote host (see
    # test_qa_guard_rejects_remote_host_regardless_of_database_name).
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "127.0.0.1:5432/production")

    with pytest.raises(RuntimeError, match="generic/production-shaped name"):
        db_client._enforce_qa_database_safety("postgresql://u:p@127.0.0.1:5432/production")


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


# --- Codex P1 findings: connection-target overrides + host allowlist ---
#
# Confirmed directly (not theoretical) before writing the fix: a fabricated
# URL with a loopback authority but a remote query-string host/port passed
# the pre-fix guard while SQLAlchemy's own PGDialect_psycopg2.
# create_connect_args() produced a DBAPI kwargs dict whose 'host' key
# disagreed with the parsed URL's authority host entirely - the query
# string wins. Every test below uses a fabricated postgresql+psycopg2 URL
# and either db_client._enforce_qa_database_safety() directly or
# db_client.get_engine() (which does not connect eagerly - create_engine()
# only opens a socket on first actual query, so even the "allowed" cases
# below never touch a network).

QA_ENV = {
    db_client._QA_ALLOW_ENV_VAR: db_client._QA_ALLOW_VALUE,
    db_client._QA_IDENTITY_ENV_VAR: DISPOSABLE_IDENTITY,
}


def _set_qa_env(monkeypatch, identity: str = DISPOSABLE_IDENTITY) -> None:
    monkeypatch.setenv(db_client._QA_ALLOW_ENV_VAR, db_client._QA_ALLOW_VALUE)
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, identity)


# 1. Local authority with remote query host override.
def test_qa_guard_rejects_local_authority_with_remote_query_host(monkeypatch):
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?host=evil.remote.invalid"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_hostaddr_override(monkeypatch):
    """hostaddr lets libpq connect to a raw IP while `host` is used only
    for TLS hostname verification - a classic DNS-rebinding-style split."""
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?hostaddr=203.0.113.1"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


# 2. Query port/database overrides.
def test_qa_guard_rejects_query_port_override(monkeypatch):
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?port=9999"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_query_dbname_override(monkeypatch):
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?dbname=production"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


# 3. Multi-host connection strings.
def test_qa_guard_rejects_multi_host_in_authority(monkeypatch):
    _set_qa_env(monkeypatch)
    # make_url() itself fails to parse a comma-joined host:port pair in
    # the authority section - the guard's own try/except around make_url
    # must turn that into a clear refusal, not an unhandled exception.
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433,evil.invalid:5432/calitrans_qa"

    with pytest.raises(RuntimeError, match="Refusing to connect"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_multi_host_via_query_string(monkeypatch):
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?host=127.0.0.1,evil.invalid"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


# 4. Unix socket and service-file routing.
def test_qa_guard_rejects_unix_socket_host(monkeypatch):
    """A host starting with '/' is libpq's unix-socket-directory
    convention, not a TCP host - bypasses host/port validation unless
    explicitly rejected by the loopback allowlist's exact-match check."""
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@%2Fvar%2Frun%2Fpostgresql:5433/calitrans_qa"

    with pytest.raises(RuntimeError, match="Refusing to connect"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_pg_service_file_routing(monkeypatch):
    """service= resolves host/port/dbname from a local pg_service.conf
    lookup that cannot be validated from the URL at all - must be
    rejected outright, never allowed through as an 'unknown but harmless'
    parameter."""
    _set_qa_env(monkeypatch)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/calitrans_qa?service=some_other_service"

    with pytest.raises(RuntimeError, match="override the connection target"):
        db_client._enforce_qa_database_safety(hostile)


# 5. Remote host with QA-shaped database name.
def test_qa_guard_rejects_remote_host_regardless_of_database_name(monkeypatch):
    """P1-2: a 'qa'-shaped database name on a remote host must never pass
    - the host allowlist is unconditional, not a fallback for when the
    name check fails."""
    remote_identity = "qa.remote-vpc.invalid:5432/calitrans_qa_disposable"
    _set_qa_env(monkeypatch, identity=remote_identity)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@qa.remote-vpc.invalid:5432/calitrans_qa_disposable"

    with pytest.raises(RuntimeError, match="approved loopback address"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_suffix_matched_hostname(monkeypatch):
    """Do not use suffix-based hostname approval - a hostname that merely
    contains/ends with a safe-looking token is not localhost."""
    remote_identity = "notlocalhost.attacker.invalid:5432/calitrans_qa"
    _set_qa_env(monkeypatch, identity=remote_identity)
    hostile = "postgresql+psycopg2://qa_user:qa_pw@notlocalhost.attacker.invalid:5432/calitrans_qa"

    with pytest.raises(RuntimeError, match="approved loopback address"):
        db_client._enforce_qa_database_safety(hostile)


def test_qa_guard_rejects_the_literal_hostname_localhost(monkeypatch):
    """'localhost' requires name resolution this guard cannot verify is
    unpoisoned - only literal loopback IPs are accepted."""
    _set_qa_env(monkeypatch, identity="localhost:5433/calitrans_qa")
    hostile = "postgresql+psycopg2://qa_user:qa_pw@localhost:5433/calitrans_qa"

    with pytest.raises(RuntimeError, match="approved loopback address"):
        db_client._enforce_qa_database_safety(hostile)


# 6. Correct loopback disposable database.
def test_qa_guard_allows_correct_loopback_disposable_database(monkeypatch):
    _set_qa_env(monkeypatch)
    db_client._enforce_qa_database_safety(DISPOSABLE_URL)  # must not raise


def test_qa_guard_allows_correct_loopback_via_get_engine(monkeypatch):
    _set_qa_env(monkeypatch)
    engine = db_client.get_engine(DISPOSABLE_URL)  # never connects eagerly
    assert engine is not None


def test_qa_guard_allows_ipv6_loopback(monkeypatch):
    identity = db_client.database_identity("postgresql+psycopg2://qa_user:qa_pw@[::1]:5433/calitrans_qa")
    _set_qa_env(monkeypatch, identity=identity)
    db_client._enforce_qa_database_safety("postgresql+psycopg2://qa_user:qa_pw@[::1]:5433/calitrans_qa")


# 7. Cached engine after guard configuration changes.
def test_cached_engine_is_not_invalidated_by_a_later_guard_failure(monkeypatch):
    """Changing CALITRANS_QA_DATABASE_IDENTITY (or any other guard input)
    after an engine for a given URL is already cached must not be able to
    retroactively mutate what that cached Engine object connects to -
    the Engine itself is immutable once created; the guard only gates
    *new* get_engine() calls for URLs not yet cached."""
    _set_qa_env(monkeypatch)
    db_client._ENGINE_CACHE.pop(DISPOSABLE_URL, None)
    engine_before = db_client.get_engine(DISPOSABLE_URL)

    # Guard configuration changes to something that would now reject
    # this exact URL if re-validated from scratch.
    monkeypatch.setenv(db_client._QA_IDENTITY_ENV_VAR, "127.0.0.1:9999/some-other-db")

    with pytest.raises(RuntimeError, match="does not match"):
        db_client.get_engine(DISPOSABLE_URL)  # guard re-runs on every call, correctly re-rejects now

    # The engine object created before the config change is untouched -
    # still the same object, still only ever configured for the
    # originally-validated URL (SQLAlchemy Engines are immutable
    # w.r.t. their connection URL).
    assert str(engine_before.url) == DISPOSABLE_URL or DISPOSABLE_IDENTITY in db_client.database_identity(DISPOSABLE_URL)


# 8. Existing Streamlit precedence and lazy-import protections.
def test_streamlit_precedence_protection_still_passes_with_new_guard_present(monkeypatch):
    """The P0 fix (DATABASE_URL env-first precedence, immune to a later
    streamlit import) and the P1 guard are independent layers - adding
    the guard must not have disturbed the P0 behavior."""
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)

    before = config.get_secret("DATABASE_URL")
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: FAKE_REAL_SECRET_URL)
    after = config.get_secret("DATABASE_URL")

    assert before == after == DISPOSABLE_URL


def test_qa_guard_runs_before_engine_cache_lookup(monkeypatch):
    """Validate before engine creation or cache lookup - a URL that was
    never cached must still be rejected by the guard, not accidentally
    treated as 'already validated' because something else happens to
    share its cache key."""
    hostile = "postgresql+psycopg2://qa_user:qa_pw@evil.remote.invalid:5432/calitrans_qa"
    db_client._ENGINE_CACHE.pop(hostile, None)
    _set_qa_env(monkeypatch, identity="evil.remote.invalid:5432/calitrans_qa")

    with pytest.raises(RuntimeError, match="approved loopback address"):
        db_client.get_engine(hostile)

    assert hostile not in db_client._ENGINE_CACHE


# --- Codex P1 finding: libpq environment variables bypass the guard ---
#
# Confirmed before writing the fix: create_connect_args() never supplies
# a 'hostaddr' key on its own (see the direct check against the real
# psycopg2 dialect above this file's test run), so libpq falls back to
# PGHOSTADDR if that environment variable is set - connecting to a raw
# IP while 'host' is used only for TLS hostname checking. host/port/
# dbname ARE always explicitly supplied, so PGHOST/PGPORT/PGDATABASE
# cannot override them per libpq's own documented precedence - but this
# guard rejects their mere presence anyway rather than relying on that
# precedence holding across every libpq/psycopg2 version. None of these
# tests open a real connection - presence-checking os.environ and
# inspecting create_connect_args()'s return value never touches a socket.

_LIBPQ_ENV_VARS = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSYSCONFDIR",
)


def _clear_libpq_env(monkeypatch) -> None:
    for name in _LIBPQ_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# PGHOSTADDR cannot redirect a loopback connection.
def test_qa_guard_rejects_pghostaddr_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")

    with pytest.raises(RuntimeError, match="PGHOSTADDR"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_get_engine_pins_hostaddr_so_a_later_pghostaddr_cannot_redirect(monkeypatch):
    """Even if PGHOSTADDR is set *after* get_engine() already validated
    and cached an engine, the created Engine's connect_args already pin
    hostaddr to the validated loopback value - libpq's own precedence
    (explicit > env var) then makes PGHOSTADDR irrelevant for this
    engine regardless of when the env var appears."""
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    db_client._ENGINE_CACHE.pop(DISPOSABLE_URL, None)

    engine = db_client.get_engine(DISPOSABLE_URL)
    assert engine.dialect.create_connect_args is not None  # sanity: real dialect object
    # The pin lives on the Engine's own connect_args, not something a
    # later env var can retroactively edit.
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    assert engine is db_client._ENGINE_CACHE[DISPOSABLE_URL]


# PGPORT cannot supply an unvalidated port.
def test_qa_guard_rejects_pgport_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGPORT", "9999")

    with pytest.raises(RuntimeError, match="PGPORT"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


# PGHOST and PGSERVICE cannot change the effective endpoint.
def test_qa_guard_rejects_pghost_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "evil.remote.invalid")

    with pytest.raises(RuntimeError, match="PGHOST"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_rejects_pgservice_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGSERVICE", "some_remote_service")

    with pytest.raises(RuntimeError, match="PGSERVICE"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_rejects_pgservicefile_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGSERVICEFILE", "/tmp/evil_pg_service.conf")

    with pytest.raises(RuntimeError, match="PGSERVICEFILE"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_rejects_pgdatabase_env_var(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGDATABASE", "production")

    with pytest.raises(RuntimeError, match="PGDATABASE"):
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)


def test_qa_guard_reports_all_present_unsafe_vars_at_once(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "evil.invalid")
    monkeypatch.setenv("PGPORT", "9999")

    with pytest.raises(RuntimeError) as exc_info:
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)
    message = str(exc_info.value)
    assert "PGHOST" in message
    assert "PGPORT" in message


def test_qa_guard_env_var_check_never_exposes_values(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "secret-internal-host.invalid")

    with pytest.raises(RuntimeError) as exc_info:
        db_client._enforce_qa_database_safety(DISPOSABLE_URL)
    assert "secret-internal-host.invalid" not in str(exc_info.value)


# Missing URL host/port/database fails closed.
def test_qa_guard_requires_explicit_port_in_url(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch, identity="127.0.0.1/calitrans_qa")

    with pytest.raises(RuntimeError, match="explicit port"):
        db_client._enforce_qa_database_safety("postgresql+psycopg2://qa_user:qa_pw@127.0.0.1/calitrans_qa")


def test_qa_guard_requires_host_in_url(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)

    with pytest.raises(RuntimeError, match="missing a host"):
        db_client._enforce_qa_database_safety("postgresql+psycopg2://qa_user:qa_pw@/calitrans_qa")


def test_qa_guard_requires_database_in_url(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)

    with pytest.raises(RuntimeError, match="missing a host"):
        db_client._enforce_qa_database_safety("postgresql+psycopg2://qa_user:qa_pw@127.0.0.1:5433/")


# Safe loopback connections still work.
def test_qa_guard_still_allows_correct_loopback_with_clean_libpq_env(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)

    result = db_client._enforce_qa_database_safety(DISPOSABLE_URL)
    assert result == "127.0.0.1"


def test_get_engine_still_works_for_loopback_with_clean_libpq_env(monkeypatch):
    _clear_libpq_env(monkeypatch)
    _set_qa_env(monkeypatch)
    db_client._ENGINE_CACHE.pop(DISPOSABLE_URL, None)

    engine = db_client.get_engine(DISPOSABLE_URL)
    assert engine is not None


# Existing 36 database safety tests remain green - enforced by running
# the whole file (pytest tests/test_database_url_safety.py), not a
# single test here; see the session's test-run evidence in the commit
# message / report rather than duplicating that run inside this file.
