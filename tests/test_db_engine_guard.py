"""PR #19 - adversarial coverage for the centralized create_engine safety guard
(pytest_db_guard). No real database is contacted: the real create_engine is
mocked so we assert the POLICY decision (reach vs. refuse), and every URL is
synthetic.

Proves the four P1 defects are closed:
  * mid-test DATABASE_URL injection cannot reach create_engine,
  * config/dotenv/module reload cannot bypass the guard,
  * production-shaped URLs are rejected,
  * missing approval yields a safe skip, and an approved disposable target is
    accepted only inside the authorized scope.
"""
from __future__ import annotations

import importlib
from unittest import mock

import pytest
from _pytest.outcomes import Skipped

import pytest_db_guard
from pytest_db_guard import DatabaseSafetyError

_PROD_URL = "postgresql://tms_app:hunter2@db.internal.example.com:5432/calitrans"
_APPROVED_URL = "postgresql://u:p@db-scratch.local:5432/calitrans_scratch_test"


@pytest.fixture
def real_engine_spy(monkeypatch):
    """Replace the REAL create_engine with a spy so an allowed target 'connects'
    to the spy (no network) and a refused target never reaches it."""
    spy = mock.Mock(name="real_create_engine", return_value=mock.sentinel.engine)
    monkeypatch.setattr(pytest_db_guard, "_REAL_CREATE_ENGINE", spy)
    return spy


def _approve(monkeypatch, url=_APPROVED_URL):
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", url)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB", "1")


# --------------------------------------------------------------------------
# #3 production-shaped URLs are rejected (and sqlite is always allowed).
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        _PROD_URL,
        "postgresql://u:p@prod-db.aws.com:5432/calitrans_scratch_test",  # prod host
        "postgresql://u:p@host:5432/postgres",  # generic db name
        "postgresql://u:p@host:5432/app",  # generic db name
        "postgresql://u:p@host:5432/randomname",  # not test/scratch named
        "mysql://u:p@host/testdb",  # non-postgres, non-sqlite
    ],
)
def test_production_shaped_or_unknown_urls_are_rejected(url):
    with pytest.raises(DatabaseSafetyError):
        pytest_db_guard.assert_engine_target_allowed(url)


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///tmp/x.db"])
def test_sqlite_targets_are_always_allowed(url):
    pytest_db_guard.assert_engine_target_allowed(url)  # no raise


# --------------------------------------------------------------------------
# #1 mid-test DATABASE_URL injection cannot reach create_engine.
# --------------------------------------------------------------------------

def test_injected_database_url_cannot_reach_create_engine(monkeypatch, real_engine_spy):
    import db_client

    pytest_db_guard.install_engine_guard()  # ensure db_client.create_engine is guarded

    # Adversary injects a production DATABASE_URL mid-test and forces db_client to
    # read it.
    monkeypatch.setenv("DATABASE_URL", _PROD_URL)
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: _PROD_URL if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(_PROD_URL, None)

    with pytest.raises(DatabaseSafetyError):
        db_client.get_engine()  # routes through the guarded create_engine
    real_engine_spy.assert_not_called()  # never reached the real engine builder


def test_direct_sqlalchemy_create_engine_is_guarded(real_engine_spy):
    import sqlalchemy

    pytest_db_guard.install_engine_guard()
    with pytest.raises(DatabaseSafetyError):
        sqlalchemy.create_engine(_PROD_URL)
    real_engine_spy.assert_not_called()


def test_migration_script_direct_create_engine_is_guarded(real_engine_spy):
    # scripts/run_migrations.py does `from sqlalchemy import create_engine` and
    # calls it directly, bypassing db_client - the guard must still catch it.
    import scripts.run_migrations as run_migrations

    pytest_db_guard.install_engine_guard()
    assert getattr(run_migrations.create_engine, "_is_db_guard", False) is True
    with pytest.raises(DatabaseSafetyError):
        run_migrations.create_engine(_PROD_URL)
    real_engine_spy.assert_not_called()


# --------------------------------------------------------------------------
# #2 config/dotenv/module reload cannot bypass the guard.
# --------------------------------------------------------------------------

def test_config_reload_does_not_bypass_guard(monkeypatch, real_engine_spy):
    import config
    import db_client

    importlib.reload(config)  # re-runs config's import-time .env side effects
    pytest_db_guard.install_engine_guard()  # autouse fixture does this each test too

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: _PROD_URL if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(_PROD_URL, None)
    with pytest.raises(DatabaseSafetyError):
        db_client.get_engine()
    real_engine_spy.assert_not_called()


def test_fresh_import_after_guard_binds_guarded_create_engine():
    import types

    # A module reload (or any fresh import) re-runs `from sqlalchemy import
    # create_engine`, which binds whatever sqlalchemy.create_engine currently is.
    # With the guard installed, that is the guarded callable - so a reload can
    # never rebind the unguarded builder. (Simulated with a throwaway module so
    # no shared module's object identity is disturbed.)
    pytest_db_guard.install_engine_guard()
    mod = types.ModuleType("synthetic_engine_user")
    exec("from sqlalchemy import create_engine", mod.__dict__)
    assert getattr(mod.create_engine, "_is_db_guard", False) is True


# --------------------------------------------------------------------------
# #4 missing approval -> safe skip; approved disposable accepted only in scope.
# --------------------------------------------------------------------------

def test_missing_approval_results_in_safe_skip(monkeypatch):
    monkeypatch.delenv("MIGRATION_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_DB", raising=False)
    with pytest.raises(Skipped):
        pytest_db_guard.require_disposable_database("MIGRATION_TEST_DATABASE_URL")


def test_url_set_but_destructive_ack_missing_skips(monkeypatch):
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_DB", raising=False)
    with pytest.raises(Skipped):
        pytest_db_guard.require_disposable_database("MIGRATION_TEST_DATABASE_URL")


def test_production_shaped_approved_var_skips_not_connects(monkeypatch):
    # Even if an operator points the approved var at a production-shaped URL, the
    # shape validation refuses it as a disposable target -> skip, never connect.
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _PROD_URL)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB", "1")
    with pytest.raises(Skipped):
        pytest_db_guard.require_disposable_database("MIGRATION_TEST_DATABASE_URL")


def test_approved_disposable_target_accepted_only_within_scope(monkeypatch, real_engine_spy):
    import sqlalchemy

    pytest_db_guard.install_engine_guard()

    # Out of scope: approved var not set -> the same URL is refused.
    monkeypatch.delenv("MIGRATION_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_DB", raising=False)
    with pytest.raises(DatabaseSafetyError):
        sqlalchemy.create_engine(_APPROVED_URL)
    real_engine_spy.assert_not_called()

    # In scope: approved + acknowledged -> the real (spied) builder is reached.
    _approve(monkeypatch)
    assert pytest_db_guard.require_disposable_database("MIGRATION_TEST_DATABASE_URL") == _APPROVED_URL
    result = sqlalchemy.create_engine(_APPROVED_URL)
    assert result is mock.sentinel.engine
    real_engine_spy.assert_called_once()
