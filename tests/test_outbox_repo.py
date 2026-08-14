"""Phase 6: repositories/outbox_repo.py tests.

enqueue_outbox_event's atomicity and idempotency-dedup behavior are
plain INSERT/transaction semantics - exercised here against a throwaway
SQLite schema (same pattern as tests/test_work_item_commands.py), which
proves real commit/rollback without needing Postgres. Its `cast(:payload
as jsonb)` clause is PostgreSQL-specific syntax that SQLite does not
reject but also does not evaluate as JSON (no jsonb type), so these
tests deliberately avoid asserting on payload *content* - only row
existence/count. claim_next_pending/mark_delivered/mark_retry/
mark_failed/reclaim_stuck_processing all use PostgreSQL-only SQL (FOR
UPDATE SKIP LOCKED, now(), make_interval) with no SQLite fallback -
those are gated behind MIGRATION_TEST_DATABASE_URL, same opt-in gate
tests/test_migration_runner.py uses, an explicit disposable-database
env var, never the app's real DATABASE_URL.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

import db_client
from repositories import outbox_repo

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


@pytest.fixture
def sqlite_outbox(monkeypatch, tmp_path):
    db_path = tmp_path / "outbox_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table loads (id integer primary key, status text)"))
        conn.execute(text("insert into loads (id, status) values (7, 'New')"))
        conn.execute(
            text(
                """
                create table outbox_events (
                    id integer primary key autoincrement,
                    event_type text not null,
                    aggregate_type text not null,
                    aggregate_id text not null,
                    payload text not null default '{}',
                    idempotency_key text not null unique,
                    status text not null default 'pending',
                    attempt_count integer not null default 0,
                    available_at timestamp not null default current_timestamp,
                    created_at timestamp not null default current_timestamp,
                    processed_at timestamp,
                    last_error text,
                    actor text
                )
                """
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _event_count(idempotency_key: str) -> int:
    df = db_client.read_df(
        "select count(*) as n from outbox_events where idempotency_key = :key", {"key": idempotency_key}
    )
    return int(df.iloc[0]["n"])


def test_enqueue_outbox_event_inserts_one_row(sqlite_outbox) -> None:
    with db_client.transaction() as conn:
        outbox_repo.enqueue_outbox_event(
            conn=conn,
            event_type="driver_dispatch_sms",
            aggregate_type="load",
            aggregate_id="7",
            payload={"to": "+15551234567", "message": "hi"},
            idempotency_key="key-1",
        )

    assert _event_count("key-1") == 1


def test_enqueue_outbox_event_same_idempotency_key_twice_is_a_no_op(sqlite_outbox) -> None:
    for _ in range(2):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="key-dup",
            )

    assert _event_count("key-dup") == 1


def test_business_write_and_outbox_enqueue_commit_together(sqlite_outbox) -> None:
    with db_client.transaction() as conn:
        conn.execute(text("update loads set status = 'Ready to Dispatch' where id = 7"))
        outbox_repo.enqueue_outbox_event(
            conn=conn,
            event_type="driver_dispatch_sms",
            aggregate_type="load",
            aggregate_id="7",
            payload={"to": "+15551234567", "message": "hi"},
            idempotency_key="key-commit-together",
        )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "Ready to Dispatch"
    assert _event_count("key-commit-together") == 1


def test_business_write_failure_rolls_back_the_outbox_enqueue_too(sqlite_outbox) -> None:
    with pytest.raises(RuntimeError):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="key-rolled-back",
            )
            raise RuntimeError("business write failed after the outbox insert")

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "New"
    assert _event_count("key-rolled-back") == 0


def test_outbox_insert_failure_rolls_back_the_business_write_too(sqlite_outbox) -> None:
    with pytest.raises(Exception):
        with db_client.transaction() as conn:
            conn.execute(text("update loads set status = 'Ready to Dispatch' where id = 7"))
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key=None,  # violates NOT NULL - the outbox insert itself fails
            )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "New"


def test_count_by_status_aggregates_and_omits_zero_statuses(sqlite_outbox) -> None:
    with db_client.transaction() as conn:
        outbox_repo.enqueue_outbox_event(
            conn=conn, event_type="driver_dispatch_sms", aggregate_type="load", aggregate_id="7",
            payload={"to": "+15551234567", "message": "hi"}, idempotency_key="count-1",
        )

    with db_client.transaction() as conn:
        counts = outbox_repo.count_by_status(conn)

    assert counts == {"pending": 1}
    assert "failed" not in counts


# ---------------------------------------------------------------------------
# PostgreSQL-only functions - claim/mark* use FOR UPDATE SKIP LOCKED, now(),
# and make_interval, none of which SQLite supports. Gated behind an
# explicit disposable-database env var, never the app's real DATABASE_URL.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable PostgreSQL database.",
)
class TestPostgresOutboxLifecycle:
    @pytest.fixture(autouse=True)
    def _schema(self, monkeypatch):
        monkeypatch.setattr(
            db_client, "get_secret", lambda name, default=None: MIGRATION_TEST_DATABASE_URL if name == "DATABASE_URL" else default
        )
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            conn.execute(text("drop table if exists outbox_events"))

        from pathlib import Path

        sql = (Path(__file__).resolve().parent.parent / "database" / "outbox_migration.sql").read_text()
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(text(statement))
        yield
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)

    def test_claim_marks_processing_and_returns_the_row(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-claim-1",
            )

        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)

        assert claimed is not None
        assert claimed["event_type"] == "driver_dispatch_sms"

        status = db_client.read_df(
            "select status from outbox_events where idempotency_key = 'pg-claim-1'"
        ).iloc[0]["status"]
        assert status == "processing"

    def test_claim_does_not_return_an_already_processing_event(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-claim-2",
            )
        with db_client.transaction() as conn:
            first = outbox_repo.claim_next_pending(conn)
        assert first is not None

        with db_client.transaction() as conn:
            second = outbox_repo.claim_next_pending(conn)
        assert second is None

    def test_mark_delivered_then_retry_then_failed_transitions(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-lifecycle",
            )
        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)

        from datetime import timedelta

        with db_client.transaction() as conn:
            outbox_repo.mark_retry(conn, claimed["id"], error="transient failure", delay=timedelta(seconds=0))
        row = db_client.read_df(
            "select status, attempt_count, last_error from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1
        assert row["last_error"] == "transient failure"

        with db_client.transaction() as conn:
            reclaimed = outbox_repo.claim_next_pending(conn)
        assert reclaimed["id"] == claimed["id"]

        with db_client.transaction() as conn:
            outbox_repo.mark_delivered(conn, claimed["id"], provider_message_id="SM123")
        row = db_client.read_df(
            "select status, processed_at from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "delivered"
        assert row["processed_at"] is not None

    def test_reclaim_uses_claimed_at_not_created_at(self):
        """Regression: reclaim must not treat a row that was created long
        ago but claimed a moment ago as stale - it was created_at-based
        staleness that had this bug in Phase 6's first pass."""
        from datetime import timedelta

        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-reclaim-fresh-claim",
            )
        with db_client.transaction() as conn:
            conn.execute(
                text("update outbox_events set created_at = now() - interval '2 hours' where idempotency_key = 'pg-reclaim-fresh-claim'")
            )
        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)
        assert claimed is not None

        # claimed_at is "now" (just claimed above); created_at is 2 hours
        # old. A reclaim window of 10 minutes must NOT touch this row.
        with db_client.transaction() as conn:
            reclaimed_count = outbox_repo.reclaim_stuck_processing(conn, older_than=timedelta(minutes=10))
        assert reclaimed_count == 0

        status = db_client.read_df(
            "select status from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]["status"]
        assert status == "processing"

    def test_reclaim_recovers_a_genuinely_stale_processing_event(self):
        from datetime import timedelta

        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-reclaim-stale",
            )
        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)
        assert claimed is not None

        with db_client.transaction() as conn:
            conn.execute(
                text("update outbox_events set claimed_at = now() - interval '1 hour' where id = :id"),
                {"id": claimed["id"]},
            )

        with db_client.transaction() as conn:
            reclaimed_count = outbox_repo.reclaim_stuck_processing(conn, older_than=timedelta(minutes=10))
        assert reclaimed_count == 1

        row = db_client.read_df(
            "select status, claimed_at from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["claimed_at"] is None

        with db_client.transaction() as conn:
            reclaimed_again = outbox_repo.claim_next_pending(conn)
        assert reclaimed_again["id"] == claimed["id"]

    def test_list_by_status_excludes_payload_and_orders_recent_first(self):
        with db_client.transaction() as conn:
            for key in ("pg-list-1", "pg-list-2"):
                outbox_repo.enqueue_outbox_event(
                    conn=conn,
                    event_type="driver_dispatch_sms",
                    aggregate_type="load",
                    aggregate_id="7",
                    payload={"to": "+15551234567", "message": "hi"},
                    idempotency_key=key,
                )

        with db_client.transaction() as conn:
            rows = outbox_repo.list_by_status(conn, "pending", limit=50)

        assert len(rows) >= 2
        assert "payload" not in rows[0]
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids, reverse=True)

    def test_get_event_includes_payload_by_id(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-get-event",
            )
        row = db_client.read_df(
            "select id from outbox_events where idempotency_key = 'pg-get-event'"
        ).iloc[0]

        with db_client.transaction() as conn:
            event = outbox_repo.get_event(conn, int(row["id"]))

        assert event is not None
        assert "payload" in event

        with db_client.transaction() as conn:
            missing = outbox_repo.get_event(conn, -1)
        assert missing is None

    def test_retry_event_only_requeues_a_failed_row_preserving_identity(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-retry-event",
            )
        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)

        with db_client.transaction() as conn:
            outbox_repo.mark_failed(conn, claimed["id"], error="terminal failure")

        # A 'pending'/'processing' row isn't eligible - only 'failed' is.
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-retry-event-still-pending",
            )
        with db_client.transaction() as conn:
            still_pending_id = db_client.read_df(
                "select id from outbox_events where idempotency_key = 'pg-retry-event-still-pending'"
            ).iloc[0]["id"]
            not_eligible = outbox_repo.retry_event(conn, int(still_pending_id))
        assert not_eligible is False

        with db_client.transaction() as conn:
            requeued = outbox_repo.retry_event(conn, claimed["id"])
        assert requeued is True

        row = db_client.read_df(
            "select id, status, attempt_count from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["id"] == claimed["id"]  # same row, not a new one
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1  # preserved, not reset (reset_attempts defaults False)

        with db_client.transaction() as conn:
            no_op = outbox_repo.retry_event(conn, claimed["id"])
        assert no_op is False  # already 'pending' now, not 'failed' - nothing to do

    def test_retry_event_with_reset_attempts_zeroes_the_counter(self):
        with db_client.transaction() as conn:
            outbox_repo.enqueue_outbox_event(
                conn=conn,
                event_type="driver_dispatch_sms",
                aggregate_type="load",
                aggregate_id="7",
                payload={"to": "+15551234567", "message": "hi"},
                idempotency_key="pg-retry-reset",
            )
        with db_client.transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)
        with db_client.transaction() as conn:
            outbox_repo.mark_failed(conn, claimed["id"], error="terminal failure")

        with db_client.transaction() as conn:
            outbox_repo.retry_event(conn, claimed["id"], reset_attempts=True)

        row = db_client.read_df(
            "select status, attempt_count, last_error from outbox_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 0
        assert row["last_error"] is None

    def test_retry_all_failed_requeues_every_failed_row(self):
        keys = ["pg-bulk-1", "pg-bulk-2", "pg-bulk-3"]
        claimed_ids = []
        for key in keys:
            with db_client.transaction() as conn:
                outbox_repo.enqueue_outbox_event(
                    conn=conn,
                    event_type="driver_dispatch_sms",
                    aggregate_type="load",
                    aggregate_id="7",
                    payload={"to": "+15551234567", "message": "hi"},
                    idempotency_key=key,
                )
            with db_client.transaction() as conn:
                claimed = outbox_repo.claim_next_pending(conn)
            with db_client.transaction() as conn:
                outbox_repo.mark_failed(conn, claimed["id"], error="terminal failure")
            claimed_ids.append(claimed["id"])

        with db_client.transaction() as conn:
            count = outbox_repo.retry_all_failed(conn)
        assert count >= len(claimed_ids)

        for event_id in claimed_ids:
            status = db_client.read_df(
                "select status from outbox_events where id = :id", {"id": event_id}
            ).iloc[0]["status"]
            assert status == "pending"
