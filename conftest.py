"""Session-wide guard: pytest must never resolve DATABASE_URL to the real
Streamlit-secrets/.env-configured database.

Root cause this closes: config.get_secret(name) checks Streamlit's own
st.secrets (which reads .streamlit/secrets.toml directly, via
_read_local_streamlit_secret as a fallback even when streamlit itself was
never imported) and .env (loaded into os.environ at config.py's own
import time, via _load_local_env_file) BEFORE the environment variable a
caller sets. Both files hold the real production DATABASE_URL in this
repository. Exporting DATABASE_URL before running pytest does NOT
override either fallback, so any test exercising an unmocked
db_client.get_engine()/read_df()/execute() call (e.g. load matching
inside classification) was silently reading live production data -
confirmed directly: with DATABASE_URL explicitly set to a disposable
Postgres instance, get_config_source("DATABASE_URL") still reported
"streamlit secrets" and get_secret("DATABASE_URL") still returned the
production URL.

Fix: neutralize every local-secret-file fallback for the whole pytest
session (get_streamlit_secret / _read_local_streamlit_secret /
_read_local_env_secret all return None, for every key, not just
DATABASE_URL - no automated test run should depend on real local secrets
for anything). Database-backed suites use this project's existing
MIGRATION_TEST_DATABASE_URL/INBOX_CERTIFICATION_DATABASE_URL convention (used directly by
tests/test_migration_runner.py, and by
tests/integration/operations_inbox/harness.py's own scratch_database()
context manager, both bypassing the application's DATABASE_URL).

This module deliberately never sets or mirrors DATABASE_URL itself -
tests/integration/operations_inbox/harness.py has its own,
already-correct safety check that refuses to run when
INBOX_CERTIFICATION_DATABASE_URL equals the app's configured DATABASE_URL
(see require_scratch_database_url), specifically to catch this exact
misconfiguration; mirroring the test URL into DATABASE_URL here would
trip that check and break certification. DATABASE_URL is always discarded
during pytest, even if the parent process already had a value. Plain
`pytest -q` therefore makes get_secret("DATABASE_URL") return None and
db_client.get_engine() raises RuntimeError("DATABASE_URL is missing...")
for any test that unexpectedly reaches it, instead of silently
succeeding against production. Tests that require a real database
already skip themselves via their own
@pytest.mark.skipif(not MIGRATION_TEST_DATABASE_URL, ...) gate, or use
the certification harness's scoped scratch_database() context manager,
and are unaffected either way.

A fourth vector, separate from the three get_secret() fallbacks above:
config.py's OWN module-level import-time side effect
(_load_local_env_file(), called unconditionally at the bottom of
config.py) copies every key from .env straight into os.environ for any
key not already set there - including DATABASE_URL, again from the same
production-valued .env file, and entirely independent of get_secret()'s
lookup order. The hook strips that value after importing config, disables
later python-dotenv loads, and strips DATABASE_URL again before each test
because service modules import dotenv independently during collection.
"""
import os


def _blocked_secret_lookup(name: str) -> str | None:
    return None


def pytest_configure(config) -> None:
    import config as _app_config  # noqa: F811 - first import may side-load .env into os.environ
    import dotenv

    # Never trust DATABASE_URL during pytest. In desktop/IDE sessions it may
    # already have been populated from the repository's local .env before
    # pytest starts, which is indistinguishable from a caller-supplied value
    # and previously allowed the application database to be contacted.
    # Database suites use their dedicated, positively validated
    # MIGRATION_TEST_DATABASE_URL / INBOX_CERTIFICATION_DATABASE_URL values.
    os.environ.pop("DATABASE_URL", None)
    _app_config.DATABASE_URL = None
    dotenv.load_dotenv = lambda *args, **kwargs: False

    _app_config.get_streamlit_secret = _blocked_secret_lookup
    _app_config._read_local_streamlit_secret = _blocked_secret_lookup
    _app_config._read_local_env_secret = _blocked_secret_lookup


def pytest_runtest_setup(item) -> None:
    """Defense in depth for modules imported during collection that copied
    local .env values after pytest_configure completed."""
    os.environ.pop("DATABASE_URL", None)
