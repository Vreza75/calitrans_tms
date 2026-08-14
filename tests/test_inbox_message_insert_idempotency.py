"""Phase 7 closure: services.operations_inbox_service::
_insert_operations_email_record_row must be safe to call twice for the
same source_message_id.

Before Phase 7's worker/retry layer, a per-message insert only ever ran
once per sync request, so a raw unique-violation on retry was never
reachable. workers/inbox_handlers.py::handle_inbox_process_message can
now retry the SAME insert after a crash between this row committing and
the worker_jobs completion write committing (the job gets reclaimed and
re-run) - without ON CONFLICT ... DO NOTHING, that retry raises a raw
IntegrityError and the job terminal-fails even though the message was
already correctly persisted by the first attempt.

Same idempotent-insert pattern (and the same SQLite-with-partial-unique-
index test fixture style) as db_client.py::add_row /
tests/test_operations_inbox_load_creation_atomicity.py's
ux_loads_source_intake_id coverage.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
from services import operations_inbox_service as ops

MESSAGE = {
    "subject": "[TMS-TEST] New Booking",
    "from": "Victor Reza <vreza75@gmail.com>",
    "body": "Test booking body",
    "received_at": "2026-07-14T09:00:00+00:00",
    "direction": "inbound",
    "mailbox": "dispatch@calitranscorp.com:INBOX",
    "mailbox_account": "dispatch@calitranscorp.com",
    "id": "1",
}


@pytest.fixture
def sqlite_order_intake(monkeypatch, tmp_path):
    db_path = tmp_path / "order_intake_idempotency_test.db"
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
                """
                create table order_intake (
                    id integer primary key autoincrement,
                    source text,
                    source_subject text,
                    source_sender text,
                    source_received_at text,
                    source_message_id text,
                    email_direction text,
                    email_mailbox text,
                    email_thread_id text,
                    email_normalized_subject text,
                    email_in_reply_to text,
                    email_references text,
                    conversation_status text,
                    email_synced_at text,
                    filename text,
                    file_path text,
                    parsed_data text,
                    raw_text text,
                    intake_status text,
                    review_status text,
                    request_type text,
                    conversation_key text,
                    matched_load_id integer,
                    confidence_score integer,
                    action_required text,
                    triage_status text,
                    triage_engine text,
                    triage_reason text,
                    triage_tags text,
                    triaged_at text,
                    work_level text,
                    department_lane text,
                    work_queue text,
                    llm_review_required integer,
                    llm_review_reason text
                )
                """
            )
        )
        # Mirrors idx_order_intake_source_message_id_unique
        # (database/operations_email_workflow_migration.sql): a partial
        # unique index, so rows with source_message_id = NULL are never
        # constrained, but two rows may not share the same non-null value.
        conn.execute(
            text(
                "create unique index idx_order_intake_source_message_id_unique "
                "on order_intake (source_message_id) where source_message_id is not null"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _row_count() -> int:
    df = db_client.read_df("select count(*) as n from order_intake")
    return int(df.iloc[0]["n"])


def test_retrying_the_same_message_insert_does_not_raise_or_duplicate(sqlite_order_intake):
    first = ops._insert_operations_email_message(MESSAGE)
    assert _row_count() == 1

    # Simulates workers/inbox_handlers.py::handle_inbox_process_message
    # being reclaimed and re-run for the same job after the first
    # attempt's insert already committed.
    second = ops._insert_operations_email_message(MESSAGE)

    assert _row_count() == 1  # no duplicate row
    assert second["message_id"] == first["message_id"]
    assert second["conversation_key"] == first["conversation_key"]


def test_two_distinct_messages_both_insert_normally(sqlite_order_intake):
    other_message = {**MESSAGE, "id": "2", "subject": "Different booking"}

    ops._insert_operations_email_message(MESSAGE)
    ops._insert_operations_email_message(other_message)

    assert _row_count() == 2
