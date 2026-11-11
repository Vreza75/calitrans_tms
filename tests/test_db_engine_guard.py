"""PR #19 - adversarial coverage for the CONNECTION-TIME database safety guard
(pytest_db_guard). No real database is contacted: rejections raise before any
network/DBAPI call, and the one "allowed" DBAPI path uses a mock. Every URL is
synthetic.

Covers the six findings:
  1. authorization enforced at actual connection time (cached engines, aliased
     create_engine, direct psycopg2.connect) - not at create_engine time;
  2. connection-routing overrides (query host, multi-host, Unix socket, service
     file, hostaddr) rejected unless the effective target is validated;
  3. exact approved-identity allowlist, no substring/generic-name escape;
  4. migration vs Operations Inbox approvals are scoped separately;
  5. the skip helper routes every destructive category through one policy;
  6. approval removed AFTER engine creation still blocks; direct DBAPI blocked.
"""
from __future__ import annotations

from unittest import mock

import pytest
import sqlalchemy
from _pytest.outcomes import Skipped

import pytest_db_guard as guard
from pytest_db_guard import DatabaseSafetyError

# identity -> ('db-scratch.example.net', 6543, 'calitrans_scratch_test')
_APPROVED_URL = "postgresql://u:p@db-scratch.example.net:6543/calitrans_scratch_test"
_APPROVED_IDENTITY = ("db-scratch.example.net", 6543, "calitrans_scratch_test")
_PROD_URL = "postgresql://tms_app:pw@db.internal.example.com:5432/calitrans"
_INBOX_URL = "postgresql://u:p@inbox-scratch.example.net:6543/inbox_scratch_test"


class _Dialect:
    def __init__(self, name: str):
        self.name = name


def _approve_migration(monkeypatch, url: str = _APPROVED_URL) -> None:
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", url)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")


def _clear_all(monkeypatch) -> None:
    for var in (
        "MIGRATION_TEST_DATABASE_URL",
        "ALLOW_DESTRUCTIVE_MIGRATION_DB",
        "INBOX_CERTIFICATION_DATABASE_URL",
        "ALLOW_DESTRUCTIVE_INBOX_CERTIFICATION_DB",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# #3 exact approved-identity allowlist (no substrings, no generic escape).
# --------------------------------------------------------------------------

def test_no_identities_approved_by_default(monkeypatch):
    _clear_all(monkeypatch)
    assert guard.approved_identities() == set()


def test_only_configured_and_acknowledged_identity_is_approved(monkeypatch):
    _clear_all(monkeypatch)
    # URL set but NOT acknowledged -> not approved.
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    assert guard.approved_identities() == set()
    # URL + acknowledgement -> exactly that identity approved.
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    assert guard.approved_identities() == {_APPROVED_IDENTITY}


@pytest.mark.parametrize(
    "url",
    [
        _PROD_URL,  # production target
        "postgresql://u:p@db.internal.example.com:5432/prod_testdb",  # 'test' substring must NOT help
        "postgresql://u:p@db-scratch.example.net:6543/other_scratch_test",  # wrong db name
        "postgresql://u:p@db-scratch.example.net:5432/calitrans_scratch_test",  # wrong port
    ],
)
def test_unapproved_or_substring_targets_are_refused(monkeypatch, url):
    _approve_migration(monkeypatch)  # approves ONLY _APPROVED_IDENTITY
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(_Dialect("postgresql"), None, [], guard._pg_cparams_from_url(url))


# --------------------------------------------------------------------------
# #2 connection-routing overrides rejected (effective target, not URL string).
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db-scratch.example.net:6543/calitrans_scratch_test?host=/var/run/pg",  # unix socket
        "postgresql://u:p@db-scratch.example.net:6543/calitrans_scratch_test?service=prod",  # service file
        "postgresql://u:p@db-scratch.example.net:6543/calitrans_scratch_test?hostaddr=10.0.0.1",  # hostaddr
    ],
)
def test_routing_overrides_are_rejected_even_for_otherwise_approved_url(monkeypatch, url):
    _approve_migration(monkeypatch)
    with pytest.raises(DatabaseSafetyError):
        guard._identity_from_cparams(guard._pg_cparams_from_url(url))


def test_query_host_override_changes_effective_identity_and_is_refused(monkeypatch):
    # URL host is the approved host, but ?host= overrides the EFFECTIVE target to
    # a production host - the guard validates the effective target, not the URL.
    _approve_migration(monkeypatch)
    sneaky = _APPROVED_URL + "?host=db.internal.example.com"
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(_Dialect("postgresql"), None, [], guard._pg_cparams_from_url(sneaky))


def test_multi_host_param_is_rejected():
    with pytest.raises(DatabaseSafetyError):
        guard._identity_from_cparams({"host": "a.example.net,b.example.net", "dbname": "x_test"})


def test_missing_host_or_db_is_rejected():
    with pytest.raises(DatabaseSafetyError):
        guard._identity_from_cparams({"dbname": "x_test"})  # no host
    with pytest.raises(DatabaseSafetyError):
        guard._identity_from_cparams({"host": "h.example.net"})  # no db


# --------------------------------------------------------------------------
# #4 / #5 per-category scoped approvals + single skip policy.
# --------------------------------------------------------------------------

def test_migration_approval_does_not_approve_inbox_identity(monkeypatch):
    _clear_all(monkeypatch)
    _approve_migration(monkeypatch)
    # Inbox URL present but its OWN ack is absent -> inbox identity not approved.
    monkeypatch.setenv("INBOX_CERTIFICATION_DATABASE_URL", _INBOX_URL)
    assert guard.approved_identities() == {_APPROVED_IDENTITY}
    inbox_identity = ("inbox-scratch.example.net", 6543, "inbox_scratch_test")
    assert inbox_identity not in guard.approved_identities()


def test_require_disposable_database_is_scoped_per_category(monkeypatch):
    _clear_all(monkeypatch)
    _approve_migration(monkeypatch)
    # migration category authorized -> returns url
    assert guard.require_disposable_database("migration") == _APPROVED_URL
    # inbox category NOT authorized -> safe skip, even though migration is
    with pytest.raises(Skipped):
        guard.require_disposable_database("inbox_certification")


def test_missing_url_and_missing_ack_both_skip(monkeypatch):
    _clear_all(monkeypatch)
    with pytest.raises(Skipped):
        guard.require_disposable_database("migration")  # no url
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    with pytest.raises(Skipped):
        guard.require_disposable_database("migration")  # url but no ack


def test_production_shaped_approved_var_skips(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _PROD_URL + "?service=prod")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    with pytest.raises(Skipped):  # routing override -> not a valid disposable target
        guard.require_disposable_database("migration")


# --------------------------------------------------------------------------
# #1 enforced at connection time: cached engines, sqlite allowed, direct DBAPI.
# --------------------------------------------------------------------------

def test_sqlite_connections_are_allowed():
    guard.install_connection_guard()
    engine = sqlalchemy.create_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select 1").scalar() == 1


def test_unapproved_postgres_engine_blocks_at_connect_not_create(monkeypatch):
    _clear_all(monkeypatch)
    guard.install_connection_guard()
    # create_engine SUCCEEDS (lazy, no validation) ...
    engine = sqlalchemy.create_engine(_PROD_URL)
    # ... the block happens at actual connection time, before any network/DBAPI.
    with pytest.raises(DatabaseSafetyError):
        engine.connect()


def test_approval_removed_after_engine_creation_still_blocks(monkeypatch):
    # Finding 6: an engine created while approved must NOT keep connecting after
    # approval is withdrawn (no stale/cached authorization).
    _approve_migration(monkeypatch)
    guard.install_connection_guard()
    engine = sqlalchemy.create_engine(_APPROVED_URL)  # lazy; no connection yet

    monkeypatch.delenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", raising=False)  # withdraw approval
    with pytest.raises(DatabaseSafetyError):
        engine.connect()  # re-validated at connect time -> refused


def test_sqlalchemy_listener_is_registered_once():
    guard.install_connection_guard()
    guard.install_connection_guard()  # idempotent
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    assert event.contains(Engine, "do_connect", guard._validate_sqlalchemy_connect)


# --------------------------------------------------------------------------
# #1 / #6 direct DBAPI (psycopg2) connect attempts are guarded.
# --------------------------------------------------------------------------

def test_direct_dbapi_connect_wrapper_blocks_unapproved(monkeypatch):
    _clear_all(monkeypatch)
    real = mock.Mock(name="real_connect", return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)

    # kwargs form (host/port/dbname) -> unapproved -> refused, real never called
    with pytest.raises(DatabaseSafetyError):
        guarded(host="db.internal.example.com", port=5432, dbname="calitrans")
    # libpq keyword-string form carrying a service override -> refused
    with pytest.raises(DatabaseSafetyError):
        guarded("host=h.example.net dbname=x_test service=prod")
    real.assert_not_called()


def test_direct_dbapi_connect_wrapper_allows_approved_target(monkeypatch):
    _approve_migration(monkeypatch)
    real = mock.Mock(name="real_connect", return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)

    result = guarded(
        host="db-scratch.example.net", port=6543, dbname="calitrans_scratch_test", user="u"
    )
    assert result is mock.sentinel.conn
    real.assert_called_once()


def test_install_wraps_psycopg2_connect():
    import importlib.util

    if importlib.util.find_spec("psycopg2") is None:
        pytest.skip("psycopg2 not installed")
    import psycopg2

    guard.install_connection_guard()
    assert getattr(psycopg2.connect, "_is_db_guard", False) is True
