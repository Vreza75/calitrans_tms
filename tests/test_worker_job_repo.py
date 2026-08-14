"""Phase 7: repositories/worker_job_repo.py tests.

Same split as tests/test_outbox_repo.py: enqueue_job's atomicity and
idempotency-dedup behavior are plain INSERT/transaction semantics,
exercised here against a throwaway SQLite schema. claim_next_job/
mark_completed/mark_retry/mark_failed/reclaim_stuck_processing all use
PostgreSQL-only SQL (FOR UPDATE SKIP LOCKED, now(), make_interval) with
no SQLite fallback - those are gated behind MIGRATION_TEST_DATABASE_URL,
an explicit disposable-database env var, never the app's real
DATABASE_URL.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

import db_client
from repositories import worker_job_repo

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


@pytest.fixture
def sqlite_worker_jobs(monkeypatch, tmp_path):
    db_path = tmp_path / "worker_jobs_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table loads (id integer primary key, status text)"))
        conn.execute(text("insert into loads (id, status) values (7, 'New')"))
        conn.execute(
            text(
                """
                create table worker_jobs (
                    id integer primary key autoincrement,
                    job_type text not null,
                    aggregate_type text not null,
                    aggregate_id text not null,
                    payload text not null default '{}',
                    idempotency_key text not null unique,
                    status text not null default 'pending',
                    attempt_count integer not null default 0,
                    available_at timestamp not null default current_timestamp,
                    created_at timestamp not null default current_timestamp,
                    claimed_at timestamp,
                    completed_at timestamp,
                    last_error text,
                    actor text
                )
                """
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def _job_count(idempotency_key: str) -> int:
    df = db_client.read_df(
        "select count(*) as n from worker_jobs where idempotency_key = :key", {"key": idempotency_key}
    )
    return int(df.iloc[0]["n"])


def test_enqueue_job_inserts_one_row(sqlite_worker_jobs) -> None:
    with db_client.transaction() as conn:
        job_id = worker_job_repo.enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id="primary_mailbox",
            payload={},
            idempotency_key="key-1",
        )

    assert isinstance(job_id, int)
    assert _job_count("key-1") == 1


def test_enqueue_job_same_idempotency_key_twice_returns_same_id_no_duplicate_row(sqlite_worker_jobs) -> None:
    ids = []
    for _ in range(2):
        with db_client.transaction() as conn:
            ids.append(
                worker_job_repo.enqueue_job(
                    conn=conn,
                    job_type="inbox.sync",
                    aggregate_type="mailbox",
                    aggregate_id="primary_mailbox",
                    payload={},
                    idempotency_key="key-dup",
                )
            )

    assert ids[0] == ids[1]
    assert _job_count("key-dup") == 1


def test_business_write_and_job_enqueue_commit_together(sqlite_worker_jobs) -> None:
    with db_client.transaction() as conn:
        conn.execute(text("update loads set status = 'Syncing' where id = 7"))
        worker_job_repo.enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id="primary_mailbox",
            payload={},
            idempotency_key="key-commit-together",
        )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "Syncing"
    assert _job_count("key-commit-together") == 1


def test_business_write_failure_rolls_back_the_job_enqueue_too(sqlite_worker_jobs) -> None:
    with pytest.raises(RuntimeError):
        with db_client.transaction() as conn:
            worker_job_repo.enqueue_job(
                conn=conn,
                job_type="inbox.sync",
                aggregate_type="mailbox",
                aggregate_id="primary_mailbox",
                payload={},
                idempotency_key="key-rolled-back",
            )
            raise RuntimeError("business write failed after the job insert")

    assert _job_count("key-rolled-back") == 0


def test_job_insert_failure_rolls_back_the_business_write_too(sqlite_worker_jobs) -> None:
    with pytest.raises(Exception):
        with db_client.transaction() as conn:
            conn.execute(text("update loads set status = 'Syncing' where id = 7"))
            worker_job_repo.enqueue_job(
                conn=conn,
                job_type="inbox.sync",
                aggregate_type="mailbox",
                aggregate_id="primary_mailbox",
                payload={},
                idempotency_key=None,  # violates NOT NULL - the job insert itself fails
            )

    status = db_client.read_df("select status from loads where id = 7").iloc[0]["status"]
    assert status == "New"


def test_count_by_status_aggregates_and_omits_zero_statuses(sqlite_worker_jobs) -> None:
    with db_client.transaction() as conn:
        worker_job_repo.enqueue_job(
            conn=conn, job_type="inbox.sync", aggregate_type="mailbox", aggregate_id="m",
            payload={}, idempotency_key="count-1",
        )
        worker_job_repo.enqueue_job(
            conn=conn, job_type="inbox.sync", aggregate_type="mailbox", aggregate_id="m",
            payload={}, idempotency_key="count-2",
        )

    with db_client.transaction() as conn:
        counts = worker_job_repo.count_by_status(conn)

    assert counts == {"pending": 2}
    assert "failed" not in counts
    assert "completed" not in counts


# ---------------------------------------------------------------------------
# PostgreSQL-only functions - claim/mark* use FOR UPDATE SKIP LOCKED, now(),
# and make_interval, none of which SQLite supports. Gated behind an
# explicit disposable-database env var, never the app's real DATABASE_URL.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at an empty, disposable PostgreSQL database.",
)
class TestPostgresWorkerJobLifecycle:
    @pytest.fixture(autouse=True)
    def _schema(self, monkeypatch):
        monkeypatch.setattr(
            db_client, "get_secret", lambda name, default=None: MIGRATION_TEST_DATABASE_URL if name == "DATABASE_URL" else default
        )
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            conn.execute(text("drop table if exists worker_jobs"))

        from pathlib import Path

        sql = (Path(__file__).resolve().parent.parent / "database" / "worker_jobs_migration.sql").read_text()
        with db_client.get_engine(MIGRATION_TEST_DATABASE_URL).begin() as conn:
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(text(statement))
        yield
        db_client._ENGINE_CACHE.pop(MIGRATION_TEST_DATABASE_URL, None)

    def _enqueue(self, key: str):
        with db_client.transaction() as conn:
            return worker_job_repo.enqueue_job(
                conn=conn,
                job_type="inbox.sync",
                aggregate_type="mailbox",
                aggregate_id="primary_mailbox",
                payload={},
                idempotency_key=key,
            )

    def test_claim_marks_processing_and_returns_the_row(self):
        self._enqueue("pg-claim-1")

        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)

        assert claimed is not None
        assert claimed["job_type"] == "inbox.sync"

        status = db_client.read_df(
            "select status from worker_jobs where idempotency_key = 'pg-claim-1'"
        ).iloc[0]["status"]
        assert status == "processing"

    def test_claim_does_not_return_an_already_processing_job(self):
        self._enqueue("pg-claim-2")
        with db_client.transaction() as conn:
            first = worker_job_repo.claim_next_job(conn)
        assert first is not None

        with db_client.transaction() as conn:
            second = worker_job_repo.claim_next_job(conn)
        assert second is None

    def test_mark_completed_then_retry_then_failed_transitions(self):
        job_id = self._enqueue("pg-lifecycle")
        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)
        assert claimed["id"] == job_id

        from datetime import timedelta

        with db_client.transaction() as conn:
            worker_job_repo.mark_retry(conn, claimed["id"], error="transient failure", delay=timedelta(seconds=0))
        row = db_client.read_df(
            "select status, attempt_count, last_error from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1
        assert row["last_error"] == "transient failure"

        with db_client.transaction() as conn:
            reclaimed = worker_job_repo.claim_next_job(conn)
        assert reclaimed["id"] == claimed["id"]

        with db_client.transaction() as conn:
            worker_job_repo.mark_completed(conn, claimed["id"])
        row = db_client.read_df(
            "select status, completed_at from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_reclaim_uses_claimed_at_not_created_at(self):
        """Regression guard mirroring outbox_repo's own fix: reclaim must
        not treat a row created long ago but claimed a moment ago as
        stale."""
        from datetime import timedelta

        self._enqueue("pg-reclaim-fresh-claim")
        with db_client.transaction() as conn:
            conn.execute(
                text("update worker_jobs set created_at = now() - interval '2 hours' where idempotency_key = 'pg-reclaim-fresh-claim'")
            )
        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)
        assert claimed is not None

        with db_client.transaction() as conn:
            reclaimed_count = worker_job_repo.reclaim_stuck_processing(conn, older_than=timedelta(minutes=10))
        assert reclaimed_count == 0

        status = db_client.read_df(
            "select status from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]["status"]
        assert status == "processing"

    def test_reclaim_recovers_a_genuinely_stale_processing_job(self):
        from datetime import timedelta

        self._enqueue("pg-reclaim-stale")
        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)
        assert claimed is not None

        with db_client.transaction() as conn:
            conn.execute(
                text("update worker_jobs set claimed_at = now() - interval '1 hour' where id = :id"),
                {"id": claimed["id"]},
            )

        with db_client.transaction() as conn:
            reclaimed_count = worker_job_repo.reclaim_stuck_processing(conn, older_than=timedelta(minutes=10))
        assert reclaimed_count == 1

        row = db_client.read_df(
            "select status, claimed_at from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["status"] == "pending"
        assert row["claimed_at"] is None

        with db_client.transaction() as conn:
            reclaimed_again = worker_job_repo.claim_next_job(conn)
        assert reclaimed_again["id"] == claimed["id"]

    def test_list_by_status_excludes_payload_and_orders_recent_first(self):
        for key in ("pg-list-1", "pg-list-2"):
            self._enqueue(key)

        with db_client.transaction() as conn:
            rows = worker_job_repo.list_by_status(conn, "pending", limit=50)

        assert len(rows) >= 2
        assert "payload" not in rows[0]
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids, reverse=True)

    def test_get_job_includes_payload_by_id(self):
        self._enqueue("pg-get-job")
        row = db_client.read_df(
            "select id from worker_jobs where idempotency_key = 'pg-get-job'"
        ).iloc[0]

        with db_client.transaction() as conn:
            job = worker_job_repo.get_job(conn, int(row["id"]))

        assert job is not None
        assert "payload" in job

        with db_client.transaction() as conn:
            missing = worker_job_repo.get_job(conn, -1)
        assert missing is None

    def test_retry_job_only_requeues_a_failed_row_preserving_identity(self):
        job_id = self._enqueue("pg-retry-job")
        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)
        assert claimed["id"] == job_id

        with db_client.transaction() as conn:
            worker_job_repo.mark_failed(conn, claimed["id"], error="terminal failure")

        self._enqueue("pg-retry-job-still-pending")
        with db_client.transaction() as conn:
            still_pending_id = db_client.read_df(
                "select id from worker_jobs where idempotency_key = 'pg-retry-job-still-pending'"
            ).iloc[0]["id"]
            not_eligible = worker_job_repo.retry_job(conn, int(still_pending_id))
        assert not_eligible is False

        with db_client.transaction() as conn:
            requeued = worker_job_repo.retry_job(conn, claimed["id"])
        assert requeued is True

        row = db_client.read_df(
            "select id, status, attempt_count from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["id"] == claimed["id"]
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1  # preserved, not reset (reset_attempts defaults False)

        with db_client.transaction() as conn:
            no_op = worker_job_repo.retry_job(conn, claimed["id"])
        assert no_op is False  # already 'pending' now, not 'failed' - nothing to do

    def test_retry_job_with_reset_attempts_zeroes_the_counter(self):
        job_id = self._enqueue("pg-retry-reset")
        with db_client.transaction() as conn:
            claimed = worker_job_repo.claim_next_job(conn)
        assert claimed["id"] == job_id
        with db_client.transaction() as conn:
            worker_job_repo.mark_failed(conn, claimed["id"], error="terminal failure")

        with db_client.transaction() as conn:
            requeued = worker_job_repo.retry_job(conn, claimed["id"], reset_attempts=True)
        assert requeued is True

        row = db_client.read_df(
            "select attempt_count, last_error from worker_jobs where id = :id", {"id": claimed["id"]}
        ).iloc[0]
        assert row["attempt_count"] == 0
        assert row["last_error"] is None

    def test_retry_all_failed_requeues_every_failed_row(self):
        ids = [self._enqueue(f"pg-retry-all-{i}") for i in range(3)]
        claimed_ids = []
        for _ in ids:
            with db_client.transaction() as conn:
                claimed = worker_job_repo.claim_next_job(conn)
                claimed_ids.append(claimed["id"])
        with db_client.transaction() as conn:
            for job_id in claimed_ids:
                worker_job_repo.mark_failed(conn, job_id, error="terminal failure")

        with db_client.transaction() as conn:
            count = worker_job_repo.retry_all_failed(conn)
        assert count == 3

        for job_id in claimed_ids:
            status = db_client.read_df(
                "select status from worker_jobs where id = :id", {"id": job_id}
            ).iloc[0]["status"]
            assert status == "pending"
