from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
import services.operations_case_service as case_service


@pytest.fixture
def sqlite_cases_db(monkeypatch, tmp_path):
    """Mirrors the columns update_operations_case/add_operations_case_note
    actually touch - proves real commit/rollback/savepoint behavior
    without needing Postgres. Same technique as
    tests/test_operations_inbox_load_creation_atomicity.py."""
    db_path = tmp_path / "cases_transaction_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table operations_cases ("
                "id integer primary key, status text, owner text, priority text, linked_load_id integer, "
                "next_action text, customer_wait_started_at text, department_wait_started_at text, "
                "closed_at text, resolved_at text, reopened_at text, updated_at text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_notes ("
                "id integer primary key autoincrement, case_id integer, note_body text, note_type text, "
                "created_by text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_owner_history ("
                "id integer primary key autoincrement, case_id integer, old_owner text, new_owner text, "
                "changed_by text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_events ("
                "id integer primary key autoincrement, case_id integer, event_type text, title text, "
                "details text, actor text, department text)"
            )
        )
        conn.execute(text("create table order_intake (id integer primary key, case_id integer, matched_load_id integer)"))
        conn.execute(
            text(
                "insert into operations_cases (id, status, owner, priority, linked_load_id, updated_at) "
                "values (1, 'New', 'Unassigned', 'Normal', NULL, '2026-01-01')"
            )
        )
        conn.execute(text("insert into order_intake (id, case_id, matched_load_id) values (1, 1, NULL)"))

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _case_row(case_id: int) -> dict:
    df = db_client.read_df("select * from operations_cases where id = :id", {"id": case_id})
    return df.iloc[0].to_dict() if not df.empty else {}


def _count(table: str) -> int:
    df = db_client.read_df(f"select count(*) as n from {table}")
    return int(df.iloc[0]["n"])


def _matched_load_id(intake_id: int):
    df = db_client.read_df("select matched_load_id from order_intake where id = :id", {"id": intake_id})
    return df.iloc[0]["matched_load_id"] if not df.empty else None


def test_update_operations_case_commits_mandatory_writes_together(sqlite_cases_db) -> None:
    case_service.update_operations_case(
        case_id=1, status="Attached to Load", owner="Dispatch", priority="Normal", linked_load_id=42
    )

    case = _case_row(1)
    assert case["status"] == "Attached to Load"
    assert case["owner"] == "Dispatch"
    assert _count("operations_case_notes") == 1
    assert _matched_load_id(1) == 42
    # Best-effort audit writes also succeeded on the happy path: one event
    # for the owner change, one for the status change.
    assert _count("operations_case_owner_history") == 1
    assert _count("operations_case_events") == 2


def test_update_operations_case_rolls_back_all_mandatory_writes_on_late_failure(monkeypatch, tmp_path) -> None:
    """order_intake is missing its case_id column, so the sync statement
    (the last mandatory write) genuinely fails at the SQL level - no
    monkeypatching of internals, a real error. The earlier mandatory
    writes in the same transaction - the operations_cases UPDATE and the
    note INSERT - must roll back too."""
    db_path = tmp_path / "cases_broken_schema_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table operations_cases ("
                "id integer primary key, status text, owner text, priority text, linked_load_id integer, "
                "next_action text, customer_wait_started_at text, department_wait_started_at text, "
                "closed_at text, resolved_at text, reopened_at text, updated_at text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_notes ("
                "id integer primary key autoincrement, case_id integer, note_body text, note_type text, "
                "created_by text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_owner_history ("
                "id integer primary key autoincrement, case_id integer, old_owner text, new_owner text, "
                "changed_by text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_events ("
                "id integer primary key autoincrement, case_id integer, event_type text, title text, "
                "details text, actor text, department text)"
            )
        )
        # No case_id column here - the mandatory order_intake sync in
        # update_operations_case must fail against this table.
        conn.execute(text("create table order_intake (id integer primary key, matched_load_id integer)"))
        conn.execute(
            text(
                "insert into operations_cases (id, status, owner, priority, linked_load_id, updated_at) "
                "values (1, 'New', 'Unassigned', 'Normal', NULL, '2026-01-01')"
            )
        )

    with pytest.raises(Exception):
        case_service.update_operations_case(
            case_id=1, status="Attached to Load", owner="Dispatch", priority="Normal", linked_load_id=42
        )

    case = _case_row(1)
    assert case["status"] == "New"
    assert case["owner"] == "Unassigned"
    assert _count("operations_case_notes") == 0

    db_client._ENGINE_CACHE.pop(url, None)


@pytest.fixture
def sqlite_cases_db_missing_audit_tables(monkeypatch, tmp_path):
    """Same as sqlite_cases_db, but deliberately omits
    operations_case_owner_history/operations_case_events entirely - any
    insert into them fails with a genuine 'no such table' error. This is
    the real regression this batch exists to prevent: PostgreSQL aborts an
    entire transaction on any statement error until rollback/savepoint-
    rollback, so without a SAVEPOINT around these best-effort inserts, a
    failure here would also roll back the mandatory case update / note
    insert right alongside it. No monkeypatching of case_service internals
    - the real update_operations_case/add_operations_case_note functions
    run unmodified against a genuinely broken audit schema."""
    db_path = tmp_path / "cases_missing_audit_tables_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table operations_cases ("
                "id integer primary key, status text, owner text, priority text, linked_load_id integer, "
                "next_action text, customer_wait_started_at text, department_wait_started_at text, "
                "closed_at text, resolved_at text, reopened_at text, updated_at text)"
            )
        )
        conn.execute(
            text(
                "create table operations_case_notes ("
                "id integer primary key autoincrement, case_id integer, note_body text, note_type text, "
                "created_by text)"
            )
        )
        # operations_case_owner_history and operations_case_events are
        # intentionally NOT created here.
        conn.execute(text("create table order_intake (id integer primary key, case_id integer, matched_load_id integer)"))
        conn.execute(
            text(
                "insert into operations_cases (id, status, owner, priority, linked_load_id, updated_at) "
                "values (1, 'New', 'Unassigned', 'Normal', NULL, '2026-01-01')"
            )
        )
        conn.execute(text("insert into order_intake (id, case_id, matched_load_id) values (1, 1, NULL)"))

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_update_operations_case_survives_missing_audit_tables(sqlite_cases_db_missing_audit_tables) -> None:
    """The best-effort owner-change and status-event writes fail (their
    target tables don't exist) but must not raise and must not block the
    mandatory case update / note / order_intake sync from committing."""
    case_service.update_operations_case(
        case_id=1, status="Attached to Load", owner="Dispatch", priority="Normal", linked_load_id=42
    )

    case = _case_row(1)
    assert case["status"] == "Attached to Load"
    assert case["owner"] == "Dispatch"
    assert _count("operations_case_notes") == 1
    assert _matched_load_id(1) == 42


def test_add_operations_case_note_commits_note_and_touch_together(sqlite_cases_db) -> None:
    case_service.add_operations_case_note(1, "Test note", note_type="internal", created_by="tester")

    assert _count("operations_case_notes") == 1
    case = _case_row(1)
    assert case["updated_at"] != "2026-01-01"
    assert _count("operations_case_events") == 1


def test_add_operations_case_note_survives_missing_audit_table(sqlite_cases_db_missing_audit_tables) -> None:
    """The best-effort event-log write fails (operations_case_events
    doesn't exist in this fixture) but must not raise and must not block
    the mandatory note insert / updated_at touch from committing."""
    case_service.add_operations_case_note(1, "Note despite missing audit table", created_by="tester")

    assert _count("operations_case_notes") == 1
    case = _case_row(1)
    assert case["updated_at"] != "2026-01-01"
