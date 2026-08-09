from __future__ import annotations

import pytest
import streamlit as st

import db_client
import services.operations_inbox_service as operations_inbox_service
from db_client import SchemaNotReadyError, SchemaReadiness, require_schema_ready


def test_returns_silently_when_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        db_client, "check_schema_readiness", lambda table, column: SchemaReadiness(ready=True, reason="ready")
    )

    require_schema_ready("widgets", "name", migration_hint="database/example_migration.sql")


def test_raises_with_migration_hint_when_schema_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        db_client,
        "check_schema_readiness",
        lambda table, column: SchemaReadiness(ready=False, reason="schema_missing"),
    )

    with pytest.raises(SchemaNotReadyError) as exc_info:
        require_schema_ready("widgets", "missing_column", migration_hint="database/example_migration.sql")

    message = str(exc_info.value)
    assert "widgets.missing_column" in message
    assert "database/example_migration.sql" in message
    assert "run_migrations.py" in message


def test_raises_without_suggesting_migrations_on_connection_error(monkeypatch) -> None:
    """A connection/permission failure is not proof the schema is missing
    - the message must not tell the operator to run migrations, since
    that would not fix a dropped connection or a permissions problem."""
    monkeypatch.setattr(
        db_client,
        "check_schema_readiness",
        lambda table, column: SchemaReadiness(ready=False, reason="connection_error", detail="could not connect"),
    )

    with pytest.raises(SchemaNotReadyError) as exc_info:
        require_schema_ready("widgets", "name", migration_hint="database/example_migration.sql")

    message = str(exc_info.value)
    assert "run_migrations.py" not in message
    assert "connection_error" in message


def test_raises_on_permission_error(monkeypatch) -> None:
    monkeypatch.setattr(
        db_client,
        "check_schema_readiness",
        lambda table, column: SchemaReadiness(ready=False, reason="permission_error", detail="access denied"),
    )

    with pytest.raises(SchemaNotReadyError, match="permission_error"):
        require_schema_ready("widgets", "name", migration_hint="database/example_migration.sql")


def test_never_calls_execute_or_transaction(monkeypatch) -> None:
    """require_schema_ready must be pure read: never open a write
    transaction, whatever the readiness outcome."""
    monkeypatch.setattr(
        db_client,
        "check_schema_readiness",
        lambda table, column: SchemaReadiness(ready=False, reason="schema_missing"),
    )

    def _boom(*args, **kwargs):
        raise AssertionError("require_schema_ready must never write to the database")

    monkeypatch.setattr(db_client, "execute", _boom)
    monkeypatch.setattr(db_client, "transaction", _boom)

    with pytest.raises(SchemaNotReadyError):
        require_schema_ready("widgets", "missing_column", migration_hint="database/example_migration.sql")


@pytest.fixture
def clean_fast_triage_flags():
    operations_inbox_service._SCHEMA_READY_FLAGS.discard("fast_triage")
    st.session_state.pop("_operations_fast_triage_schema_ready", None)
    yield
    operations_inbox_service._SCHEMA_READY_FLAGS.discard("fast_triage")
    st.session_state.pop("_operations_fast_triage_schema_ready", None)


def test_ensure_fast_triage_schema_raises_when_migration_not_applied(monkeypatch, clean_fast_triage_flags) -> None:
    monkeypatch.setattr(
        db_client,
        "check_schema_readiness",
        lambda table, column: SchemaReadiness(ready=False, reason="schema_missing"),
    )

    with pytest.raises(SchemaNotReadyError, match="order_intake.llm_review_reason"):
        operations_inbox_service.ensure_operations_fast_triage_schema()

    assert "fast_triage" not in operations_inbox_service._SCHEMA_READY_FLAGS


def test_ensure_fast_triage_schema_succeeds_and_caches_when_ready(monkeypatch, clean_fast_triage_flags) -> None:
    calls = {"n": 0}

    def _fake_readiness(table, column):
        calls["n"] += 1
        return SchemaReadiness(ready=True, reason="ready")

    monkeypatch.setattr(db_client, "check_schema_readiness", _fake_readiness)

    operations_inbox_service.ensure_operations_fast_triage_schema()
    assert "fast_triage" in operations_inbox_service._SCHEMA_READY_FLAGS
    assert calls["n"] == 1

    # Second call must short-circuit on the cached flag, not re-check.
    operations_inbox_service.ensure_operations_fast_triage_schema()
    assert calls["n"] == 1
