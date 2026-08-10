from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
import services.operations_inbox_service as operations_inbox_service


@pytest.fixture
def sqlite_inbox_db(monkeypatch, tmp_path):
    """A throwaway sqlite schema shaped like the columns
    create_load_from_inbox_item/update_load_from_inbox_item actually touch -
    proves real commit/rollback atomicity without needing Postgres. Same
    approach as tests/test_work_item_commands.py's sqlite_order_intake
    fixture. A `now()` SQL function is registered on the connection since
    the production SQL (written for Postgres) calls now() directly."""
    db_path = tmp_path / "inbox_atomicity_test.db"
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
                "create table loads ("
                "id integer primary key autoincrement, type text, booking_number text, "
                "reference_number text, customer text, status text, updated_at text, "
                "source_intake_id integer)"
            )
        )
        # Mirrors database/loads_source_intake_idempotency_migration.sql's
        # ux_loads_source_intake_id: a partial unique index, so rows with
        # source_intake_id = NULL (manual/legacy load creation) are never
        # constrained, but two rows may not share the same non-null value.
        conn.execute(
            text(
                "create unique index ux_loads_source_intake_id "
                "on loads (source_intake_id) where source_intake_id is not null"
            )
        )
        conn.execute(
            text(
                "create table status_events ("
                "id integer primary key autoincrement, load_id integer, old_status text, "
                "new_status text, notes text, created_by text)"
            )
        )
        conn.execute(
            text(
                "create table order_intake ("
                "id integer primary key, review_status text, intake_status text, "
                "matched_load_id integer, linked_load_id integer, request_type text, "
                "conversation_key text, reviewed_at text, reviewed_by text)"
            )
        )
        conn.execute(
            text(
                "create table load_communications ("
                "id integer primary key autoincrement, load_id integer, intake_id integer, "
                "case_id integer, conversation_key text, communication_type text, "
                "direction text, subject text, sender text, message_body text)"
            )
        )
        conn.execute(
            text(
                "insert into order_intake (id, review_status, matched_load_id, conversation_key) "
                "values (1, 'Needs Review', NULL, '')"
            )
        )
        conn.execute(
            text(
                "insert into order_intake (id, review_status, matched_load_id, conversation_key) "
                "values (2, 'Needs Review', NULL, '')"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _loads_count() -> int:
    df = db_client.read_df("select count(*) as n from loads")
    return int(df.iloc[0]["n"])


def _communications_count() -> int:
    df = db_client.read_df("select count(*) as n from load_communications")
    return int(df.iloc[0]["n"])


def _review_status(intake_id: int) -> str:
    df = db_client.read_df("select review_status from order_intake where id = :id", {"id": intake_id})
    return df.iloc[0]["review_status"]


def _load_customer(load_id: int) -> str | None:
    df = db_client.read_df("select customer from loads where id = :id", {"id": load_id})
    return df.iloc[0]["customer"] if not df.empty else None


def test_create_load_from_inbox_item_commits_all_writes_together(sqlite_inbox_db) -> None:
    result = operations_inbox_service.create_load_from_inbox_item(
        1,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )

    assert result["load_id"] is not None
    assert _loads_count() == 1
    assert _communications_count() == 1
    assert _review_status(1) == "Order Created"


def test_create_load_from_inbox_item_rolls_back_every_write_on_late_failure(sqlite_inbox_db, monkeypatch) -> None:
    """The loads insert (via create_load_from_intake, which also updates
    order_intake.intake_status) happens before _save_load_communication
    runs. Forcing that last step to fail must undo the earlier writes too -
    proving the whole function is one transaction, not independent commits."""

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure while saving communication")

    monkeypatch.setattr(operations_inbox_service, "_save_load_communication", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        operations_inbox_service.create_load_from_inbox_item(
            1,
            {"Booking Number": "TEST123"},
            subject="New booking",
            body="Please book this load.",
        )

    assert _loads_count() == 0
    assert _communications_count() == 0
    assert _review_status(1) == "Needs Review"


def test_update_load_from_inbox_item_rolls_back_on_late_failure(sqlite_inbox_db, monkeypatch) -> None:
    db_client.execute(
        "insert into loads (id, type, booking_number, status) values (1, 'Import', 'EXIST123', 'New')"
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure while saving communication")

    monkeypatch.setattr(operations_inbox_service, "_save_load_communication", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        operations_inbox_service.update_load_from_inbox_item(
            1,
            1,
            {"Customer": "Acme Corp"},
            subject="Update",
            body="Please update this load.",
        )

    assert _communications_count() == 0
    assert _review_status(1) == "Needs Review"
    # The load-field update (Customer) ran before the forced failure - it
    # must be rolled back along with everything else, not left applied.
    assert _load_customer(1) is None


def _loads_for_source(source_intake_id: int) -> list[int]:
    df = db_client.read_df(
        "select id from loads where source_intake_id = :source_intake_id", {"source_intake_id": source_intake_id}
    )
    return df["id"].tolist()


def test_sequential_retry_does_not_duplicate_the_load(sqlite_inbox_db) -> None:
    """Test B: processing the exact same intake source twice (e.g. a
    re-synced email, a retried API call) must leave exactly one load and
    one communication, and resolve to the same canonical load both times -
    not raise, not silently create a second row."""
    first = operations_inbox_service.create_load_from_inbox_item(
        1,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )
    second = operations_inbox_service.create_load_from_inbox_item(
        1,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )

    assert first["load_id"] == second["load_id"]
    assert _loads_count() == 1
    assert _communications_count() == 1
    assert _review_status(1) == "Order Created"
    assert len(_loads_for_source(1)) == 1


def test_rollback_then_retry_creates_exactly_one_load(sqlite_inbox_db, monkeypatch) -> None:
    """Test C: a failure on the first attempt must roll back fully (proven
    already by the rollback tests above); a normal retry afterward must
    then succeed cleanly, creating exactly one load - not zero (blocked by
    leftover state) and not two."""
    original_save = operations_inbox_service._save_load_communication
    call_count = {"n": 0}

    def _fail_once(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated failure while saving communication")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(operations_inbox_service, "_save_load_communication", _fail_once)

    with pytest.raises(RuntimeError, match="simulated failure"):
        operations_inbox_service.create_load_from_inbox_item(
            1,
            {"Booking Number": "TEST123"},
            subject="New booking",
            body="Please book this load.",
        )

    assert _loads_count() == 0

    result = operations_inbox_service.create_load_from_inbox_item(
        1,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )

    assert result["load_id"] is not None
    assert _loads_count() == 1
    assert _communications_count() == 1
    assert _review_status(1) == "Order Created"


def test_distinct_sources_with_identical_business_data_both_create_loads(sqlite_inbox_db) -> None:
    """Test D: idempotency must key off source identity (work_item_id),
    never off parsed business content. Two different intake rows with the
    identical booking number/subject/body are two legitimate loads, not a
    detected duplicate."""
    first = operations_inbox_service.create_load_from_inbox_item(
        1,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )
    second = operations_inbox_service.create_load_from_inbox_item(
        2,
        {"Booking Number": "TEST123"},
        subject="New booking",
        body="Please book this load.",
    )

    assert first["load_id"] != second["load_id"]
    assert _loads_count() == 2
    assert _communications_count() == 2


def test_database_enforces_one_load_per_source_intake_id(sqlite_inbox_db) -> None:
    """Test E: exercise the unique index itself, independent of
    add_row's application-level ON CONFLICT handling - proves the
    constraint, not just the Python code path, is what prevents two
    canonical loads for the same source."""
    db_client.execute(
        "insert into loads (id, booking_number, source_intake_id) values (100, 'A1', 1)"
    )

    with pytest.raises(Exception):
        db_client.execute(
            "insert into loads (id, booking_number, source_intake_id) values (101, 'A2', 1)"
        )

    assert _loads_count() == 1

    # NULL source_intake_id (manual/legacy creation) is never constrained -
    # any number of such rows may coexist.
    db_client.execute("insert into loads (id, booking_number, source_intake_id) values (102, 'A3', NULL)")
    db_client.execute("insert into loads (id, booking_number, source_intake_id) values (103, 'A4', NULL)")

    assert _loads_count() == 3
