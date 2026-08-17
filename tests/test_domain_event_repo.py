"""Phase 9: repositories/domain_event_repo.py tests. Same structure as
tests/test_outbox_repo.py (see that file's docstring for why claim/mark*
are PostgreSQL-gated and enqueue/count are plain SQLite-testable
transaction semantics).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

import db_client
from repositories import domain_event_repo

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


@pytest.fixture
def sqlite_domain_events(monkeypatch, tmp_path):
    db_path = tmp_path / "domain_events_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table loads (id integer primary key, status text)"))
        conn.execute(text("insert into loads (id, status) values (7, 'New')"))
        conn.execute(
            text(
                """
                create table domain_events (
                    id integer primary key autoincrement,
                    event_type text not null,
                    aggregate_type text not null,
                    aggregate_id text not null,
                    version text,
                    payload text not null default '{}',
                    idempotency_key text not null unique,
                    status text not null default 'pending',
                    attempt_count integer not null default 0,
                    available_at timestamp not null default current_timestamp,
                    created_at timestamp not null default current_timestamp,
                    published_at timestamp,
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
        "select count(*) as n from domain_events where idempotency_key = :key", {"key": idempotency_key}
    )
    return int(df.iloc[0]["n"])


def test_enqueue_domain_event_inserts_one_row(sqlite_domain_events) -> None:
    with db_client.transaction() as conn:
        domain_event_repo.enqueue_domain_event(
            conn=conn,
            event_type="load.status_changed",
            aggregate_type="load",
            aggregate_id="7",
            payload={"new_status": "Dispatched"},
            idempotency_key="key-1",
        )

    assert _event_count("key-1") == 1


def test_enqueue_domain_event_same_idempotency_key_twice_is_a_no_op(sqlite_domain_events) -> None:
    for _ in range(2):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn,
                event_type="load.status_changed",
                aggregate_type="load",
                aggregate_id="7",
                payload={"new_status": "Dispatched"},
                idempotency_key="key-dup",
            )

    assert _event_count("key-dup") == 1


def test_business_write_and_event_enqueue_commit_together(sqlite_domain_events) -> None:
    with db_client.transaction() as conn:
        conn.execute(text("update loads set status = 'Dispatched' where id = 7"))
        domain_event_repo.enqueue_domain_event(
            conn=conn,
            event_type="load.status_changed",
            aggregate_type="load",
            aggregate_id="7",
            payload={"new_status": "Dispatched"},
            idempotency_key="key-commit-together",
        )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "Dispatched"
    assert _event_count("key-commit-together") == 1


def test_business_write_failure_rolls_back_the_event_enqueue_too(sqlite_domain_events) -> None:
    with pytest.raises(RuntimeError):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn,
                event_type="load.status_changed",
                aggregate_type="load",
                aggregate_id="7",
                payload={"new_status": "Dispatched"},
                idempotency_key="key-rolled-back",
            )
            raise RuntimeError("business write failed after the event insert")

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "New"
    assert _event_count("key-rolled-back") == 0


def test_event_insert_failure_rolls_back_the_business_write_too(sqlite_domain_events) -> None:
    with pytest.raises(Exception):
        with db_client.transaction() as conn:
            conn.execute(text("update loads set status = 'Dispatched' where id = 7"))
            domain_event_repo.enqueue_domain_event(
                conn=conn,
                event_type="load.status_changed",
                aggregate_type="load",
                aggregate_id="7",
                payload={"new_status": "Dispatched"},
                idempotency_key=None,  # violates NOT NULL - the event insert itself fails
            )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "New"


def test_count_by_status_aggregates_and_omits_zero_statuses(sqlite_domain_events) -> None:
    with db_client.transaction() as conn:
        domain_event_repo.enqueue_domain_event(
            conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
            payload={"new_status": "Dispatched"}, idempotency_key="count-1",
        )

    with db_client.transaction() as conn:
        counts = domain_event_repo.count_by_status(conn)

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
class TestPostgresDomainEventLifecycle:
    @pytest.fixture(autouse=True)
    def _schema(self, monkeypatch):
        monkeypatch.setattr(
            db_client, "get_secret", lambda name, default=None: MIGRATION_TEST_DATABASE_URL if name == "DATABASE_URL" else default
        )
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            conn.execute(text("drop table if exists domain_events"))

        from pathlib import Path

        sql = (Path(__file__).resolve().parent.parent / "database" / "domain_events_migration.sql").read_text()
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(text(statement))
        yield
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)

    def test_claim_marks_processing_and_returns_the_row(self):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-claim-1",
            )

        with db_client.transaction() as conn:
            claimed = domain_event_repo.claim_next_pending(conn)

        assert claimed is not None
        assert claimed["event_type"] == "load.status_changed"

        status = db_client.read_df(
            "select status from domain_events where idempotency_key = 'pg-claim-1'"
        ).iloc[0]["status"]
        assert status == "processing"

    def test_claim_does_not_return_an_already_processing_event(self):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-claim-2",
            )
        with db_client.transaction() as conn:
            first = domain_event_repo.claim_next_pending(conn)
        assert first is not None

        with db_client.transaction() as conn:
            second = domain_event_repo.claim_next_pending(conn)
        assert second is None

    def test_mark_published_then_retry_then_failed_transitions(self):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-lifecycle",
            )
        with db_client.transaction() as conn:
            claimed = domain_event_repo.claim_next_pending(conn)

        from datetime import timedelta

        with db_client.transaction() as conn:
            domain_event_repo.mark_retry(conn, claimed["id"], error="transient failure", delay=timedelta(seconds=0))
        row = db_client.read_df(
            "select status, attempt_count, last_error from domain_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1
        assert row["last_error"] == "transient failure"

        with db_client.transaction() as conn:
            reclaimed = domain_event_repo.claim_next_pending(conn)
        assert reclaimed["id"] == claimed["id"]

        with db_client.transaction() as conn:
            domain_event_repo.mark_published(conn, claimed["id"])
        row = db_client.read_df(
            "select status, published_at from domain_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "published"
        assert row["published_at"] is not None

    def test_reclaim_recovers_a_genuinely_stale_processing_event(self):
        from datetime import timedelta

        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-reclaim-stale",
            )
        with db_client.transaction() as conn:
            claimed = domain_event_repo.claim_next_pending(conn)
        assert claimed is not None

        with db_client.transaction() as conn:
            conn.execute(
                text("update domain_events set claimed_at = now() - interval '1 hour' where id = :id"),
                {"id": claimed["id"]},
            )

        with db_client.transaction() as conn:
            reclaimed_count = domain_event_repo.reclaim_stuck_processing(conn, older_than=timedelta(minutes=10))
        assert reclaimed_count == 1

        row = db_client.read_df(
            "select status, claimed_at from domain_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["claimed_at"] is None

    def test_retry_event_only_requeues_a_failed_row_preserving_identity(self):
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-retry-event",
            )
        with db_client.transaction() as conn:
            claimed = domain_event_repo.claim_next_pending(conn)
        with db_client.transaction() as conn:
            domain_event_repo.mark_failed(conn, claimed["id"], error="terminal failure")

        with db_client.transaction() as conn:
            requeued = domain_event_repo.retry_event(conn, claimed["id"])
        assert requeued is True

        row = db_client.read_df(
            "select id, status, attempt_count from domain_events where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["id"] == claimed["id"]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1

    def test_multiple_publishers_cannot_claim_the_same_event(self):
        """STEP 28: FOR UPDATE SKIP LOCKED under two concurrent
        connections - the second claim attempt must not return the row
        the first connection already claimed and hasn't committed/
        released yet. Uses two separate connections to simulate two
        publisher processes racing for the same row."""
        with db_client.transaction() as conn:
            domain_event_repo.enqueue_domain_event(
                conn=conn, event_type="load.status_changed", aggregate_type="load", aggregate_id="7",
                payload={"new_status": "Dispatched"}, idempotency_key="pg-concurrent-claim",
            )

        engine = db_client.get_engine(MIGRATION_TEST_DATABASE_URL)
        conn_a = engine.connect()
        txn_a = conn_a.begin()
        try:
            claimed_a = domain_event_repo.claim_next_pending(conn_a)
            assert claimed_a is not None

            with db_client.transaction() as conn_b:
                claimed_b = domain_event_repo.claim_next_pending(conn_b)
            assert claimed_b is None  # row A holds is skip-locked, not returned to B
        finally:
            txn_a.commit()
            conn_a.close()
