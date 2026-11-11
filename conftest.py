"""Session-wide guard: pytest must never resolve DATABASE_URL to the real
application database, by ANY path - inherited from the shell, injected from
.env via dotenv at config import time, read from .streamlit/secrets.toml, or
reintroduced by a late dotenv reload / import-order variation.

Root cause this closes: config.get_secret(name) consults Streamlit's own
st.secrets, a local .streamlit/secrets.toml read, and a local .env read as
fallbacks, and config.py ALSO has two import-time side effects (dotenv's
load_dotenv() and the hand-rolled _load_local_env_file()) that copy every key
from .env straight into os.environ - including the production DATABASE_URL in
this repository. Exporting a different DATABASE_URL before pytest did not
reliably override those, and simply inheriting an ambient DATABASE_URL (which
could itself be production) silently pointed tests at a live database.

Fix (defense in depth, all idempotent - see _install_database_isolation):
  * Replace every file-backed secret fallback (get_streamlit_secret,
    _read_local_streamlit_secret, _read_local_env_secret) with a lookup that
    returns None for EVERY key, so get_secret() resolves purely from os.environ.
  * Neutralize config's own dotenv/.env loaders (load_dotenv, _load_local_env_file)
    so a reload or explicit re-invocation cannot re-inject DATABASE_URL.
  * Strip DATABASE_URL from os.environ entirely. pytest NEVER silently uses the
    application DATABASE_URL. Approved integration tests do not need it: they use
    their own explicit, disposable-database env vars - MIGRATION_TEST_DATABASE_URL
    (tests/test_migration_runner.py, @skipif-gated) and
    INBOX_CERTIFICATION_DATABASE_URL (tests/integration/operations_inbox/harness.py,
    which bypasses config entirely via its scratch_database() override and already
    refuses to run against a URL equal to the app's configured DATABASE_URL). We
    deliberately do NOT copy either test URL into DATABASE_URL.

Timing: _install_database_isolation() runs (1) at this module's import, which
pytest performs before collecting/importing any test module - so config's
import-time .env side effects are stripped immediately, even though importing
config here is what first triggers them; (2) again in pytest_configure; and
(3) again via an autouse fixture at the start of every test, so a prior test
that reloaded config/dotenv cannot leak a DATABASE_URL into a later one. The
operation is idempotent, so running it repeatedly is harmless.
"""
import os

import pytest

import pytest_db_guard


def _blocked_secret_lookup(name: str) -> str | None:
    return None


def _blocked_dotenv(*args, **kwargs) -> bool:
    return False


def _blocked_env_loader(*args, **kwargs) -> None:
    return None


def _install_database_isolation() -> None:
    """Neutralize every path to the real DATABASE_URL and strip any value already
    present. Idempotent: safe to call at import, in pytest_configure, and per test."""
    import config as app_config  # importing (first time) triggers config's .env side effects

    app_config.get_streamlit_secret = _blocked_secret_lookup
    app_config._read_local_streamlit_secret = _blocked_secret_lookup
    app_config._read_local_env_secret = _blocked_secret_lookup
    app_config.load_dotenv = _blocked_dotenv
    app_config._load_local_env_file = _blocked_env_loader
    os.environ.pop("DATABASE_URL", None)
    # Last-resort net: every SQLAlchemy create_engine (db_client AND the direct
    # calls in scripts/*, certification harness) is routed through the fail-closed
    # test-database policy, so no test can open a connection to a real or unknown
    # database even via mid-test injection, dotenv/config reload, or a bypass of
    # db_client.get_engine().
    pytest_db_guard.install_engine_guard()


# Earliest safe point: runs before any test module imports config/db_client.
_install_database_isolation()


@pytest.fixture(autouse=True)
def _enforce_database_isolation():
    """Re-assert isolation at the start of every test, defending against a prior
    test that reloaded config / dotenv, re-injected DATABASE_URL, or imported a
    module that freshly bound an unguarded create_engine."""
    _install_database_isolation()
    yield


def pytest_configure(config) -> None:
    _install_database_isolation()


@pytest.fixture
def disposable_db(monkeypatch):
    """Bind db_client to an explicitly-approved DISPOSABLE PostgreSQL database for
    the few tests that need a real DB round trip (schema DDL, column_exists).

    Routes through the central policy (pytest_db_guard.require_disposable_database):
    skips unless MIGRATION_TEST_DATABASE_URL is set, destructive access is
    acknowledged, and the target passes disposable-shape validation. The URL is
    bound by patching db_client.get_secret (never by writing DATABASE_URL into the
    environment), so these tests can ONLY ever touch the operator-approved
    disposable database, never a silently-inherited app one - and the create_engine
    guard enforces the same policy at connect time regardless."""
    url = pytest_db_guard.require_disposable_database("MIGRATION_TEST_DATABASE_URL", scope="disposable_db")

    import db_client

    original_get_secret = db_client.get_secret

    def _forced_get_secret(name: str, default: str | None = None):
        if name == "DATABASE_URL":
            return url
        return original_get_secret(name, default)

    monkeypatch.setattr(db_client, "get_secret", _forced_get_secret)
    db_client._ENGINE_CACHE.pop(url, None)
    try:
        yield url
    finally:
        db_client._ENGINE_CACHE.pop(url, None)


# Email-driven load creation defaults OFF in production (the learning-phase
# switch EMAIL_LOAD_CREATION_ENABLED, see
# services/operations_inbox_service.py::email_load_creation_enabled). Rather
# than force it ON for the whole suite (which would mask the production default
# everywhere), enable it ONLY for the specific modules that exercise the
# load-creation mechanics. Every other test runs with the production default
# (off). The dedicated gate tests assert both states explicitly.
_EMAIL_LOAD_CREATION_TEST_MODULES = {
    "test_operations_inbox_load_creation_atomicity",
    "test_case_006_one_booking_four_containers",
    "test_work_item_command_authorization",
    "test_application_loads_commands_error_sanitization",
    "test_api_error_sanitization",
    "test_api_work_items",
    "test_operations_multi_container_service",
    "test_operations_multi_container",
}


@pytest.fixture(autouse=True)
def _email_load_creation_flag(request, monkeypatch):
    """Enable email-driven load creation ONLY for the modules that exercise the
    creation mechanics - every other test keeps the production default (off),
    so the switch is never enabled suite-wide."""
    module_name = getattr(getattr(request, "module", None), "__name__", "").rsplit(".", 1)[-1]
    if module_name in _EMAIL_LOAD_CREATION_TEST_MODULES:
        monkeypatch.setenv("EMAIL_LOAD_CREATION_ENABLED", "true")
