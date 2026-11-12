"""Regression coverage for the root conftest.py production-DB isolation
guard (see conftest.py's own docstring for the full root-cause writeup).

Reproduces, directly, the exact defect found while adding a real
attachment fixture to the segmentation-quarantine-final pass: with no
test-database env var set, config.get_secret("DATABASE_URL") resolved to
the real production URL from .streamlit/secrets.toml/.env, via three
independent fallback paths - two inside get_secret()'s own precedence
chain, and a third from config.py's unconditional _load_local_env_file()
import-time side effect that copies .env straight into os.environ. All
three are neutralized for the whole pytest session by the root
conftest.py's pytest_configure hook, which has already run by the time
this test file executes.
"""
import os

import config


def test_database_url_is_not_resolvable_without_an_explicit_env_var():
    """The root conftest.py guard has already run for this whole session
    (pytest_configure fires before any test module is collected) - by the
    time this assertion runs, DATABASE_URL must not be silently populated
    from a local secrets file, only from an env var the invoking shell
    explicitly set (MIGRATION_TEST_DATABASE_URL/
    INBOX_CERTIFICATION_DATABASE_URL/DATABASE_URL - none of which this
    test's own CI/dev invocation sets by default)."""
    assert os.environ.get("DATABASE_URL") is None
    assert config.get_secret("DATABASE_URL") is None


def test_streamlit_secret_fallbacks_are_neutralized_for_every_key():
    """Not just DATABASE_URL - no automated test run should be able to
    silently read any real local secret."""
    assert config.get_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_streamlit_secret("DATABASE_URL") is None
    assert config._read_local_env_secret("DATABASE_URL") is None


def test_get_engine_fails_loudly_instead_of_reaching_production():
    import db_client

    try:
        db_client.get_engine()
    except RuntimeError as exc:
        assert "DATABASE_URL is missing" in str(exc)
    else:
        raise AssertionError("get_engine() unexpectedly succeeded with no database configured")


def test_migration_test_database_url_never_leaks_into_the_app_database_url(monkeypatch):
    """A disposable-database opt-in var (MIGRATION_TEST_DATABASE_URL /
    INBOX_CERTIFICATION_DATABASE_URL) must never be silently picked up as
    the app's own DATABASE_URL by unrelated tests - each database-backed
    suite forces it into db_client.get_secret explicitly (see
    tests/integration/operations_inbox/harness.py's scratch_database()),
    it is never read implicitly. Guards the PR-01 acceptance criterion
    that only an explicitly, positively supplied disposable identity is
    ever used - the presence of *a* disposable URL in the environment must
    not widen what plain, unrelated tests can reach."""
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", "postgresql://scratch:pw@127.0.0.1:1/scratch_db")
    monkeypatch.setenv("INBOX_CERTIFICATION_DATABASE_URL", "postgresql://scratch:pw@127.0.0.1:1/scratch_db")

    assert config.get_secret("DATABASE_URL") is None

    import db_client

    try:
        db_client.get_engine()
    except RuntimeError as exc:
        assert "DATABASE_URL is missing" in str(exc)
    else:
        raise AssertionError("get_engine() unexpectedly resolved a database with no DATABASE_URL configured")


def test_connection_failures_do_not_leak_credentials_in_the_raised_error(monkeypatch):
    """Static/behavioral proof for the PR-01 'sanitize database URLs from
    failures and tracebacks' acceptance criterion: even when db_client is
    forced onto a bogus credentialed URL, the exception surfaced by a
    failed connection attempt must not echo the username or password back
    (SQLAlchemy/psycopg2 already omit them for connection-refused errors;
    this pins that behavior so a driver/library upgrade that changed it
    would be caught here instead of in a live traceback)."""
    import db_client

    bogus_url = "postgresql://tms_app:Sup3rS3cret!@127.0.0.1:1/does-not-exist"
    monkeypatch.setattr(
        db_client,
        "get_secret",
        lambda name, default=None: bogus_url if name == "DATABASE_URL" else default,
    )
    db_client._ENGINE_CACHE.pop(bogus_url, None)

    try:
        with db_client.get_engine().connect():
            raise AssertionError("connection to an unroutable port unexpectedly succeeded")
    except AssertionError:
        raise
    except Exception as exc:
        message = str(exc)
        assert "Sup3rS3cret!" not in message
        assert "tms_app" not in message
    finally:
        db_client._ENGINE_CACHE.pop(bogus_url, None)
