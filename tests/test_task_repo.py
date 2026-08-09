from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
import repositories.task_repo as task_repo


@pytest.fixture
def sqlite_tasks_db(monkeypatch, tmp_path):
    """Same technique as tests/test_user_repo.py - real SQLite proves real
    commit/read behavior without needing Postgres. Regression coverage for
    create_task() previously using read_df() (engine.connect(), no
    auto-commit), which returned a valid-looking id while the row was never
    committed - verified live against Postgres during the independent audit
    that found this bug."""
    db_path = tmp_path / "tasks_test.db"
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
                "create table operations_tasks ("
                "id integer primary key autoincrement, task_title text, task_description text, "
                "task_status text, task_priority text, owner text, due_at text, "
                "source_type text, intake_id integer, case_id integer, load_id integer, "
                "customer text, booking_number text, container_number text, "
                "created_by text, completed_by text, completed_at text, "
                "created_at text, updated_at text)"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_create_task_returns_new_id(sqlite_tasks_db) -> None:
    task_id = task_repo.create_task(title="Call customer about LFD")
    assert isinstance(task_id, int)
    assert task_id > 0


def test_create_task_actually_persists_the_row(sqlite_tasks_db) -> None:
    """The regression this test locks in: a returned id must correspond to
    a row that is really committed and independently readable, not one
    that vanishes when the insert connection closes."""
    task_id = task_repo.create_task(title="Verify POD received", owner="Accounting", priority="High")

    engine = db_client.get_engine(sqlite_tasks_db)
    with engine.connect() as conn:
        row = conn.execute(
            text("select task_title, owner, task_priority from operations_tasks where id = :id"),
            {"id": task_id},
        ).first()

    assert row is not None
    assert row[0] == "Verify POD received"
    assert row[1] == "Accounting"
    assert row[2] == "High"


def test_create_task_requires_title(sqlite_tasks_db) -> None:
    with pytest.raises(ValueError):
        task_repo.create_task(title="   ")


def test_create_task_defaults_invalid_owner_and_priority(sqlite_tasks_db) -> None:
    task_id = task_repo.create_task(title="Follow up", owner="Not A Real Owner", priority="Not A Real Priority")

    engine = db_client.get_engine(sqlite_tasks_db)
    with engine.connect() as conn:
        row = conn.execute(
            text("select owner, task_priority from operations_tasks where id = :id"),
            {"id": task_id},
        ).first()

    assert row[0] == "Dispatch"
    assert row[1] == "Medium"


def test_missing_schema_raises_schema_not_ready_not_silent_create(monkeypatch, tmp_path) -> None:
    """No runtime DDL: querying against a database that never had
    dispatcher_workspace_migration.sql applied must fail loudly, not
    self-heal."""
    db_path = tmp_path / "no_tasks_table.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)
    with engine.begin() as conn:
        conn.execute(text("create table unrelated (id integer primary key)"))

    with pytest.raises(db_client.SchemaNotReadyError):
        task_repo.create_task(title="Should not create")

    db_client._ENGINE_CACHE.pop(url, None)
