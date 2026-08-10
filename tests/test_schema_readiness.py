"""Phase 1 correction (Codex finding): column_exists() collapsed every
exception - including a dropped connection or a permission error - into
"column not found", which meant a render-path readiness check couldn't
tell a genuinely missing schema apart from a connectivity problem.
check_schema_readiness() fixes that; these tests cover the classification
logic directly (mocked) and, where a disposable database is configured,
prove zero DDL statements are ever issued by the readiness check itself.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import db_client

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


class _FakeOrig:
    def __init__(self, pgcode: str | None):
        self.pgcode = pgcode


class _FakeConnCtx:
    def __init__(self, result_or_exc):
        self._result_or_exc = result_or_exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        if isinstance(self._result_or_exc, Exception):
            raise self._result_or_exc
        return self._result_or_exc


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeEngine:
    def __init__(self, result_or_exc):
        self._result_or_exc = result_or_exc

    def connect(self):
        return _FakeConnCtx(self._result_or_exc)


def _wire_engine(monkeypatch, result_or_exc):
    monkeypatch.setattr(db_client, "get_engine", lambda url: _FakeEngine(result_or_exc))


def test_ready_when_column_found(monkeypatch):
    _wire_engine(monkeypatch, _FakeResult(("1",)))
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert readiness.ready is True
    assert readiness.reason == "ready"


def test_schema_missing_when_query_succeeds_but_no_row(monkeypatch):
    _wire_engine(monkeypatch, _FakeResult(None))
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert readiness.ready is False
    assert readiness.reason == "schema_missing"


def test_connection_error_is_not_reported_as_schema_missing(monkeypatch):
    exc = OperationalError("select 1", {}, _FakeOrig("08006"))
    _wire_engine(monkeypatch, exc)
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert readiness.ready is False
    assert readiness.reason == "connection_error"


def test_permission_error_is_not_reported_as_schema_missing(monkeypatch):
    exc = ProgrammingError("select 1", {}, _FakeOrig("42501"))
    _wire_engine(monkeypatch, exc)
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert readiness.ready is False
    assert readiness.reason == "permission_error"


def test_unrecognized_error_falls_back_to_unknown_not_schema_missing(monkeypatch):
    _wire_engine(monkeypatch, RuntimeError("something unexpected"))
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert readiness.ready is False
    assert readiness.reason == "unknown_error"


def test_error_detail_redacts_embedded_credentials(monkeypatch):
    exc = OperationalError(
        "connection to server at postgresql://appuser:hunter2@db.internal:5432/prod failed",
        {},
        _FakeOrig("08006"),
    )
    _wire_engine(monkeypatch, exc)
    readiness = db_client.check_schema_readiness("order_intake", "case_id")
    assert "hunter2" not in readiness.detail
    assert "appuser" not in readiness.detail


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable PostgreSQL database.",
)
def test_readiness_check_issues_zero_ddl_against_a_real_database(monkeypatch):
    """Real proof, not a mock: instruments actual SQL sent to a disposable
    Postgres and asserts the readiness check never sends CREATE/ALTER/DROP."""
    import config

    url = MIGRATION_TEST_DATABASE_URL
    configured = None
    try:
        configured = config.get_secret("DATABASE_URL")
    except Exception:
        configured = None
    if configured and configured.strip() == url.strip():
        pytest.fail("MIGRATION_TEST_DATABASE_URL is identical to the app's configured DATABASE_URL - refusing to run.")

    original_get_secret = db_client.get_secret

    def _forced_get_secret(name, default=None):
        if name == "DATABASE_URL":
            return url
        return original_get_secret(name, default)

    monkeypatch.setattr(db_client, "get_secret", _forced_get_secret)
    db_client._ENGINE_CACHE.pop(url, None)

    from sqlalchemy import event

    statements: list[str] = []
    engine = db_client.get_engine(url)

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        db_client.check_schema_readiness("order_intake", "case_id")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db_client._ENGINE_CACHE.pop(url, None)

    ddl_keywords = ("create table", "alter table", "create index", "create trigger", "drop trigger", "drop table")
    for statement in statements:
        lowered = statement.lower()
        assert not any(keyword in lowered for keyword in ddl_keywords), statement
    assert len(statements) >= 1
