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
    # Independent positive disposability evidence (correction 3): approval now
    # also requires the identity to be enumerated here, not just URL + ack.
    monkeypatch.setenv("DISPOSABLE_DB_IDENTITIES", url)


def _clear_all(monkeypatch) -> None:
    for var in (
        "MIGRATION_TEST_DATABASE_URL",
        "ALLOW_DESTRUCTIVE_MIGRATION_DB",
        "INBOX_CERTIFICATION_DATABASE_URL",
        "ALLOW_DESTRUCTIVE_INBOX_CERTIFICATION_DB",
        "DISPOSABLE_DB_IDENTITIES",
        "FORBIDDEN_DATABASE_IDENTITIES",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# #3 exact approved-identity allowlist (no substrings, no generic escape).
# --------------------------------------------------------------------------

def test_no_identities_approved_by_default(monkeypatch):
    _clear_all(monkeypatch)
    assert guard.approved_identities() == set()


def test_url_ack_and_positive_evidence_all_required_for_approval(monkeypatch):
    _clear_all(monkeypatch)
    # URL set but NOT acknowledged -> not approved.
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    assert guard.approved_identities() == set()
    # URL + ack but NO positive disposability evidence -> still not approved.
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    assert guard.approved_identities() == set()
    # URL + ack + DISPOSABLE_DB_IDENTITIES -> exactly that identity approved.
    monkeypatch.setenv("DISPOSABLE_DB_IDENTITIES", _APPROVED_URL)
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


# --------------------------------------------------------------------------
# Correction 1 - mixed DSN + routing kwargs cannot smuggle a different target.
# --------------------------------------------------------------------------

def test_dbapi_dsn_plus_kwarg_host_override_is_validated_as_effective_target(monkeypatch):
    _approve_migration(monkeypatch)
    real = mock.Mock(return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)
    # Positional DSN is approved, but a host= kwarg (libpq precedence) would move
    # the real connection to production - must be refused on the EFFECTIVE target.
    with pytest.raises(DatabaseSafetyError):
        guarded(_APPROVED_URL, host="db.internal.example.com")
    real.assert_not_called()


def test_dbapi_dsn_kwarg_is_normalized_and_allowed_when_approved(monkeypatch):
    _approve_migration(monkeypatch)
    real = mock.Mock(return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)
    assert guarded(dsn=_APPROVED_URL) is mock.sentinel.conn  # no over-rejection
    real.assert_called_once()


def test_dbapi_kwarg_host_override_wins_over_dsn_for_approval(monkeypatch):
    # The reverse: DSN points at production, but kwargs move it to the approved
    # target -> the effective (approved) target is what gets validated + allowed.
    _approve_migration(monkeypatch)
    real = mock.Mock(return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)
    result = guarded(
        _PROD_URL, host="db-scratch.example.net", port=6543, dbname="calitrans_scratch_test"
    )
    assert result is mock.sentinel.conn


# --------------------------------------------------------------------------
# Correction 2 - pooled connection re-validated on checkout (revocation).
# --------------------------------------------------------------------------

class _ConnRecord:
    def __init__(self):
        self.info = {}


def test_pool_checkout_revalidates_and_blocks_after_revocation(monkeypatch):
    _approve_migration(monkeypatch)
    record = _ConnRecord()
    # do_connect stamps the validated identity onto the pooled record.
    guard._validate_sqlalchemy_connect(_Dialect("postgresql"), record, [], guard._pg_cparams_from_url(_APPROVED_URL))
    assert record.info[guard._CONN_RECORD_IDENTITY_KEY] == _APPROVED_IDENTITY
    # While still approved, checkout passes.
    guard._validate_pool_checkout(object(), record, object())
    # Revoke approval mid-session -> the SAME pooled connection is refused.
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", raising=False)
    with pytest.raises(DatabaseSafetyError):
        guard._validate_pool_checkout(object(), record, object())


def test_pool_checkout_allows_unstamped_verifiable_sqlite():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        guard._validate_pool_checkout(conn, _ConnRecord(), object())  # unstamped sqlite -> allowed
    finally:
        conn.close()


def test_pool_checkout_rejects_unstamped_non_sqlite_connection():
    # Defect 3: an unstamped record whose raw connection is NOT verifiably sqlite
    # (e.g. a PostgreSQL connection created before the listener installed) must be
    # refused, not auto-allowed.
    class _FakePgConn:
        pass

    _FakePgConn.__module__ = "psycopg2"
    with pytest.raises(DatabaseSafetyError):
        guard._validate_pool_checkout(_FakePgConn(), _ConnRecord(), object())
    # An unknown/opaque connection object is likewise refused.
    with pytest.raises(DatabaseSafetyError):
        guard._validate_pool_checkout(object(), _ConnRecord(), object())


def test_checkout_listener_is_registered():
    guard.install_connection_guard()
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    assert event.contains(Engine, "checkout", guard._validate_pool_checkout)


# --------------------------------------------------------------------------
# Correction 3 - independent production backstop (deny + positive evidence).
# --------------------------------------------------------------------------

def test_forbidden_identity_is_denied_even_when_otherwise_approved(monkeypatch):
    _approve_migration(monkeypatch)  # url + ack + evidence for the approved id
    # Operator ALSO (mistakenly) lists it as forbidden -> deny wins.
    monkeypatch.setenv("FORBIDDEN_DATABASE_IDENTITIES", _APPROVED_URL)
    assert guard.approved_identities() == set()
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(
            _Dialect("postgresql"), _ConnRecord(), [], guard._pg_cparams_from_url(_APPROVED_URL)
        )


def test_url_and_ack_without_evidence_is_refused_at_connect(monkeypatch):
    _clear_all(monkeypatch)
    # Naming + acknowledgement alone must NOT authorize (no positive evidence).
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(
            _Dialect("postgresql"), _ConnRecord(), [], guard._pg_cparams_from_url(_APPROVED_URL)
        )


def test_compact_host_port_db_evidence_token_is_accepted(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    monkeypatch.setenv("DISPOSABLE_DB_IDENTITIES", "db-scratch.example.net:6543/calitrans_scratch_test")
    assert guard.approved_identities() == {_APPROVED_IDENTITY}


# --------------------------------------------------------------------------
# Correction 4 - reload re-wraps; aliases/limitation documented.
# --------------------------------------------------------------------------

def test_install_rewraps_psycopg_connect_after_reload(monkeypatch):
    import importlib.util

    if importlib.util.find_spec("psycopg2") is None:
        pytest.skip("psycopg2 not installed")
    import psycopg2

    guard.install_connection_guard()
    # Simulate a reload restoring the original (unmarked) connect.
    guarded_before = psycopg2.connect
    original = getattr(guarded_before, "_real_connect", guarded_before)
    monkeypatch.setattr(psycopg2, "connect", original, raising=False)
    assert getattr(psycopg2.connect, "_is_db_guard", False) is False
    # Next install must re-wrap it.
    guard.install_connection_guard()
    assert getattr(psycopg2.connect, "_is_db_guard", False) is True


def test_network_isolation_limitation_is_documented():
    assert "network-isolated" in guard.__doc__
    assert "alias" in guard.__doc__.lower()


# --------------------------------------------------------------------------
# Defect 1 (P0) - database/dbname alias precedence (both scenarios).
# --------------------------------------------------------------------------

def test_dsn_dbname_approved_plus_kwarg_database_unapproved_is_refused(monkeypatch):
    _approve_migration(monkeypatch)
    real = mock.Mock(return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)
    # DSN carries the approved dbname; a conflicting `database` kwarg would make
    # the driver refuse (and must not be validated as the approved target).
    with pytest.raises(DatabaseSafetyError):
        guarded(_APPROVED_URL, database="some_production_db")
    real.assert_not_called()


def test_dsn_database_approved_plus_kwarg_dbname_unapproved_is_refused(monkeypatch):
    _approve_migration(monkeypatch)
    real = mock.Mock(return_value=mock.sentinel.conn)
    guarded = guard._make_guarded_dbapi_connect(real)
    # libpq keyword/value DSN using the `database` alias, plus a conflicting
    # `dbname` kwarg -> ambiguous effective target -> refused.
    dsn = "host=db-scratch.example.net port=6543 database=calitrans_scratch_test"
    with pytest.raises(DatabaseSafetyError):
        guarded(dsn, dbname="some_production_db")
    real.assert_not_called()


def test_database_alias_alone_resolves_to_dbname(monkeypatch):
    _approve_migration(monkeypatch)
    # Only the `database` alias is supplied (no dbname) -> resolves to the same
    # identity and is approved.
    ident = guard._identity_from_cparams(
        {"host": "db-scratch.example.net", "port": 6543, "database": "calitrans_scratch_test"}
    )
    assert ident == _APPROVED_IDENTITY


def test_matching_dbname_and_database_are_accepted():
    ident = guard._identity_from_cparams(
        {"host": "h.example.net", "port": 6543, "dbname": "x_test", "database": "x_test"}
    )
    assert ident == ("h.example.net", 6543, "x_test")


# --------------------------------------------------------------------------
# Defect 2 (P1) - strict, fail-closed parsing of backstop identity lists.
# --------------------------------------------------------------------------

def test_malformed_forbidden_entry_refuses_all_postgres(monkeypatch):
    _approve_migration(monkeypatch)  # a fully-approved target...
    # ...but the deny list has a malformed entry (bad port) -> cannot be parsed.
    monkeypatch.setenv("FORBIDDEN_DATABASE_IDENTITIES", "db-scratch.example.net:notaport/db")
    with pytest.raises(DatabaseSafetyError):
        guard.approved_identities()
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(
            _Dialect("postgresql"), _ConnRecord(), [], guard._pg_cparams_from_url(_APPROVED_URL)
        )
    assert guard.category_authorized("migration") is False  # fail closed, no hard error


def test_malformed_disposable_entry_refuses_all_postgres(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("MIGRATION_TEST_DATABASE_URL", _APPROVED_URL)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_DB", "1")
    # Evidence list carries a malformed entry (multi-host) -> strict reject.
    monkeypatch.setenv("DISPOSABLE_DB_IDENTITIES", "a.example.net,b.example.net/db_test")
    with pytest.raises(DatabaseSafetyError):
        guard._validate_sqlalchemy_connect(
            _Dialect("postgresql"), _ConnRecord(), [], guard._pg_cparams_from_url(_APPROVED_URL)
        )
    assert guard.category_authorized("migration") is False


def test_malformed_entry_is_never_silently_discarded(monkeypatch):
    monkeypatch.setenv("FORBIDDEN_DATABASE_IDENTITIES", "mysql://u@h/db")  # non-postgres -> malformed
    with pytest.raises(DatabaseSafetyError):
        guard.forbidden_identities()
