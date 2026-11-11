"""Tests for the unattended scheduled inbox.sync producer
(application/inbox/commands.py::request_scheduled_inbox_sync,
application/inbox/queries.py::get_inbox_sync_health,
scripts/enqueue_inbox_sync.py).

Two tiers:
- Pure unit tests (no database): system-identity (never a dispatcher),
  overlap guard, interval-bucketed idempotency key, configurable cadence,
  and the CLI's exit codes.
- Real-PostgreSQL tests gated behind MIGRATION_TEST_DATABASE_URL (an
  explicit opt-in disposable database; never the app's real DATABASE_URL):
  enqueue creates one pending job, an immediate re-run creates no duplicate,
  a completed job lets the next interval enqueue again, and health reports
  backlog / last success / sanitized failure.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest import mock

import pytest
import pytest_db_guard

import db_client
import repositories.worker_job_repo as worker_job_repo
from application.inbox import commands

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")


# --------------------------------------------------------------------------
# Unit tests - no database
# --------------------------------------------------------------------------

@contextmanager
def _fake_txn():
    yield object()


def test_scheduled_sync_enqueues_under_system_identity(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(db_client, "transaction", _fake_txn)
    monkeypatch.setattr(worker_job_repo, "try_acquire_mailbox_sync_lock", lambda conn, name: True)
    monkeypatch.setattr(worker_job_repo, "has_active_job", lambda conn, job_type: False)
    monkeypatch.setattr(worker_job_repo, "job_exists_for_idempotency_key", lambda conn, key: False)
    monkeypatch.setattr(worker_job_repo, "enqueue_job", lambda **kw: (captured.update(kw) or 777))

    result = commands.request_scheduled_inbox_sync(now=0.0)

    assert result.ok is True
    assert result.status == "queued"
    assert result.job_id == 777
    # System identity, never a dispatcher/human role.
    assert captured["actor"] == "system:inbox-scheduler"
    assert captured["actor"] != "dispatcher"
    assert captured["job_type"] == "inbox.sync"
    assert captured["idempotency_key"].startswith("inbox.sync:scheduled:primary_mailbox:")


def test_scheduled_sync_takes_no_authenticated_actor():
    """Signature guard: the scheduled path must not accept (and so cannot
    impersonate) a dispatcher AuthenticatedActor - that is the interactive
    request_inbox_sync's job, with its own permission check."""
    import inspect

    params = inspect.signature(commands.request_scheduled_inbox_sync).parameters
    assert "actor" not in params


def test_scheduled_sync_skips_when_a_sync_is_already_active(monkeypatch):
    enqueue_called = {"n": 0}
    monkeypatch.setattr(db_client, "transaction", _fake_txn)
    monkeypatch.setattr(worker_job_repo, "try_acquire_mailbox_sync_lock", lambda conn, name: True)
    monkeypatch.setattr(worker_job_repo, "has_active_job", lambda conn, job_type: True)
    monkeypatch.setattr(
        worker_job_repo, "enqueue_job", lambda **kw: enqueue_called.update(n=enqueue_called["n"] + 1)
    )

    result = commands.request_scheduled_inbox_sync(now=0.0)

    assert result.ok is True
    assert result.status == "skipped"
    assert result.job_id is None
    assert "already pending or processing" in result.reason
    assert enqueue_called["n"] == 0, "overlap guard must not enqueue a second sync"


def test_scheduled_sync_skips_when_another_producer_holds_the_lock(monkeypatch):
    """Concurrency guard: if the mailbox advisory lock is held by another
    scheduled producer, this one skips and enqueues nothing - without even
    reaching the overlap/same-window checks."""
    enqueue_called = {"n": 0}
    monkeypatch.setattr(db_client, "transaction", _fake_txn)
    monkeypatch.setattr(worker_job_repo, "try_acquire_mailbox_sync_lock", lambda conn, name: False)

    def _should_not_run(*a, **k):
        raise AssertionError("must not reach the per-job guards or enqueue when the lock is held")

    monkeypatch.setattr(worker_job_repo, "has_active_job", _should_not_run)
    monkeypatch.setattr(worker_job_repo, "job_exists_for_idempotency_key", _should_not_run)
    monkeypatch.setattr(
        worker_job_repo, "enqueue_job", lambda **kw: enqueue_called.update(n=enqueue_called["n"] + 1)
    )

    result = commands.request_scheduled_inbox_sync(now=0.0)

    assert result.status == "skipped"
    assert result.job_id is None
    assert "lock held" in result.reason
    assert enqueue_called["n"] == 0


def test_scheduled_sync_skips_when_same_interval_job_exists(monkeypatch):
    """A completed/failed job for the current interval window must yield an
    honest "skipped", not a "queued" that routes through enqueue_job's
    no-op ON CONFLICT and returns a terminal job's id."""
    enqueue_called = {"n": 0}
    monkeypatch.setattr(db_client, "transaction", _fake_txn)
    monkeypatch.setattr(worker_job_repo, "try_acquire_mailbox_sync_lock", lambda conn, name: True)
    monkeypatch.setattr(worker_job_repo, "has_active_job", lambda conn, job_type: False)
    monkeypatch.setattr(worker_job_repo, "job_exists_for_idempotency_key", lambda conn, key: True)
    monkeypatch.setattr(
        worker_job_repo, "enqueue_job", lambda **kw: enqueue_called.update(n=enqueue_called["n"] + 1)
    )

    result = commands.request_scheduled_inbox_sync(now=0.0)

    assert result.status == "skipped"
    assert result.job_id is None
    assert "this interval window" in result.reason
    assert enqueue_called["n"] == 0


def test_idempotency_key_buckets_by_interval():
    key_a = commands._scheduled_inbox_sync_idempotency_key(interval_minutes=5, now=0.0)
    key_b = commands._scheduled_inbox_sync_idempotency_key(interval_minutes=5, now=299.0)  # same 5-min bucket
    key_c = commands._scheduled_inbox_sync_idempotency_key(interval_minutes=5, now=300.0)  # next bucket
    assert key_a == key_b, "fires within one interval collapse to one job"
    assert key_a != key_c, "the next interval gets a fresh job"


def test_interval_minutes_default_and_override(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_secret", lambda name, default=None: default)
    assert commands._sync_interval_minutes() == 5

    monkeypatch.setattr(config, "get_secret", lambda name, default=None: "10")
    assert commands._sync_interval_minutes() == 10

    # Garbage / non-positive values fall back to the safe default.
    monkeypatch.setattr(config, "get_secret", lambda name, default=None: "not-a-number")
    assert commands._sync_interval_minutes() == 5
    monkeypatch.setattr(config, "get_secret", lambda name, default=None: "0")
    assert commands._sync_interval_minutes() == 5


def test_cli_enqueue_reports_queued_and_skipped(monkeypatch, capsys):
    from application.inbox.models import InboxSyncRequestResult
    import scripts.enqueue_inbox_sync as cli

    monkeypatch.setattr(
        "application.inbox.commands.request_scheduled_inbox_sync",
        lambda: InboxSyncRequestResult(ok=True, job_id=55, status="queued"),
    )
    assert cli._cmd_enqueue(None) == 0
    assert "queued (job_id=55)" in capsys.readouterr().out

    monkeypatch.setattr(
        "application.inbox.commands.request_scheduled_inbox_sync",
        lambda: InboxSyncRequestResult(ok=True, job_id=None, status="skipped", reason="busy"),
    )
    assert cli._cmd_enqueue(None) == 0
    assert "skipped: busy" in capsys.readouterr().out


def test_scheduled_job_type_is_processable_by_the_worker():
    """The producer's job_type must be a registered handler, or scheduled
    jobs would sit unprocessed. Guards against a drift between producer and
    processor."""
    from workers.processor import JOB_HANDLERS

    assert "inbox.sync" in JOB_HANDLERS


def test_fetch_failure_reason_distinguishes_job_run_from_mailbox_retrieval():
    """A completed inbox.sync must mean a mailbox was actually logged into,
    not merely that the job ran. _fetch_failure_reason turns a zero-login
    fetch into a job failure so it surfaces in health, while a healthy empty
    inbox (>=1 login) stays a success."""
    from workers.inbox_handlers import _fetch_failure_reason

    # Nothing could be attempted (e.g. all credentials missing) -> failure.
    assert _fetch_failure_reason(
        {"accounts_attempted": 0, "errors": ["account: Missing password"], "per_account": []}
    )
    # Nothing attempted, no errors (nothing configured) -> preserved no-op success.
    assert _fetch_failure_reason({"accounts_attempted": 0, "errors": [], "per_account": []}) == ""
    # Accounts attempted but EVERY login failed -> failure (the P2 gap).
    reason = _fetch_failure_reason(
        {
            "accounts_attempted": 2,
            "errors": ["a: auth", "b: auth"],
            "per_account": [{"login_success": False}, {"login_success": False}],
        }
    )
    assert reason and "login" in reason.lower()
    # Messages fetched -> retrieval unambiguously happened, success even if the
    # diagnostics flags are incomplete (a mailbox clearly answered).
    assert (
        _fetch_failure_reason(
            {"accounts_attempted": 2, "errors": [], "per_account": []}, fetched_count=3
        )
        == ""
    )
    # At least one login succeeded -> success even with a partial error.
    assert (
        _fetch_failure_reason(
            {
                "accounts_attempted": 2,
                "errors": ["b: auth"],
                "per_account": [{"login_success": True}, {"login_success": False}],
            }
        )
        == ""
    )


# --------------------------------------------------------------------------
# Real-PostgreSQL tests - opt-in, disposable database only
# --------------------------------------------------------------------------

requires_db = pytest.mark.skipif(
    not pytest_db_guard.category_authorized("migration"),
    reason="Set MIGRATION_TEST_DATABASE_URL to a disposable Postgres to run scheduled-sync DB tests.",
)


@contextmanager
def _scratch_database(url: str):
    original_get_secret = db_client.get_secret

    def _forced_get_secret(name: str, default: str | None = None):
        if name == "DATABASE_URL":
            return url
        return original_get_secret(name, default)

    with mock.patch.object(db_client, "get_secret", _forced_get_secret):
        db_client._ENGINE_CACHE.pop(url, None)
        yield
        db_client._ENGINE_CACHE.pop(url, None)


@pytest.fixture
def scratch_db():
    from scripts.run_migrations import run as run_migrations

    url = MIGRATION_TEST_DATABASE_URL
    assert run_migrations(url) == 0, "Migration run against the disposable database failed."
    with _scratch_database(url):
        db_client.execute("delete from worker_jobs where job_type = 'inbox.sync'", {})
        yield url
        db_client.execute("delete from worker_jobs where job_type = 'inbox.sync'", {})


def _count_inbox_sync(status: str | None = None) -> int:
    sql = "select count(*) as n from worker_jobs where job_type = 'inbox.sync'"
    params: dict = {}
    if status is not None:
        sql += " and status = :status"
        params["status"] = status
    return int(db_client.read_df(sql, params).iloc[0]["n"])


@requires_db
def test_db_enqueue_creates_one_pending_job(scratch_db):
    result = commands.request_scheduled_inbox_sync(now=0.0)
    assert result.status == "queued"
    assert _count_inbox_sync("pending") == 1
    # Recorded under the system identity, not a dispatcher.
    actor = db_client.read_df(
        "select actor from worker_jobs where id = :id", {"id": result.job_id}
    ).iloc[0]["actor"]
    assert actor == "system:inbox-scheduler"


@requires_db
def test_db_repeated_run_does_not_duplicate_while_active(scratch_db):
    first = commands.request_scheduled_inbox_sync(now=0.0)
    second = commands.request_scheduled_inbox_sync(now=1.0)  # same interval, job still pending
    assert first.status == "queued"
    assert second.status == "skipped"
    assert _count_inbox_sync() == 1, "overlap guard must prevent a duplicate pending sync"


@requires_db
def test_db_next_interval_enqueues_again_after_completion(scratch_db):
    first = commands.request_scheduled_inbox_sync(now=0.0)
    # Simulate the worker finishing the first job so nothing is active.
    db_client.execute(
        "update worker_jobs set status='completed', claimed_at=now(), completed_at=now() where id=:id",
        {"id": first.job_id},
    )
    # A later interval must be allowed a fresh job (future runs still work).
    second = commands.request_scheduled_inbox_sync(now=600.0)
    assert second.status == "queued"
    assert second.job_id != first.job_id
    assert _count_inbox_sync() == 2


@requires_db
def test_db_health_reports_backlog_success_and_failure(scratch_db):
    from application.inbox.queries import get_inbox_sync_health

    done = commands.request_scheduled_inbox_sync(now=0.0)
    db_client.execute(
        "update worker_jobs set status='completed', claimed_at=now() - interval '3 seconds', "
        "completed_at=now() where id=:id",
        {"id": done.job_id},
    )
    failed = commands.request_scheduled_inbox_sync(now=600.0)
    db_client.execute(
        "update worker_jobs set status='failed', last_error='sanitized failure detail' where id=:id",
        {"id": failed.job_id},
    )

    health = get_inbox_sync_health()
    assert health.completed == 1
    assert health.failed == 1
    assert health.last_success_at is not None
    assert health.last_duration_seconds is not None and health.last_duration_seconds >= 2
    assert health.last_error == "sanitized failure detail"


@requires_db
def test_db_stuck_processing_job_is_reclaimed_so_sync_resumes(scratch_db):
    """A worker crash can leave an inbox.sync stuck 'processing'. The overlap
    guard skips once, but process_pending's reclaim recovers the job so the
    sync actually runs and the next interval enqueues again - the guard can
    never block syncing forever."""
    from datetime import timedelta

    from db_client import transaction
    from repositories.worker_job_repo import claim_next_job, mark_completed, reclaim_stuck_processing

    # Crashed worker: an inbox.sync claimed 20 minutes ago, never finished.
    db_client.execute(
        "insert into worker_jobs "
        "(job_type, aggregate_type, aggregate_id, payload, idempotency_key, actor, status, claimed_at) "
        "values ('inbox.sync','mailbox','primary_mailbox', cast('{}' as jsonb), "
        "'inbox.sync:stuck-test','system:inbox-scheduler','processing', now() - interval '20 minutes')",
        {},
    )

    # One skip while the stuck job is still (apparently) processing.
    assert commands.request_scheduled_inbox_sync(now=0.0).status == "skipped"

    # Reclaim resets the stale 'processing' row to 'pending' ...
    with transaction() as conn:
        assert reclaim_stuck_processing(conn, older_than=timedelta(minutes=15)) == 1
    # ... and it is now claimable, so a worker will actually run the sync.
    with transaction() as conn:
        claimed = claim_next_job(conn)
    assert claimed is not None and claimed["job_type"] == "inbox.sync"
    with transaction() as conn:
        mark_completed(conn, claimed["id"])

    # Syncing resumes: a later interval enqueues a fresh job.
    resumed = commands.request_scheduled_inbox_sync(now=3600.0)
    assert resumed.status == "queued"
    assert resumed.job_id is not None


@requires_db
def test_db_same_window_conflict_skips_and_never_reverts_terminal(scratch_db):
    """A second fire in the same interval window after the job already
    completed must report "skipped" (not "queued") and must not revert the
    completed job."""
    first = commands.request_scheduled_inbox_sync(now=0.0)
    assert first.status == "queued"
    db_client.execute(
        "update worker_jobs set status='completed', claimed_at=now(), completed_at=now() where id=:id",
        {"id": first.job_id},
    )

    second = commands.request_scheduled_inbox_sync(now=1.0)  # same 5-min bucket
    assert second.status == "skipped"
    assert second.job_id is None

    status = db_client.read_df(
        "select status from worker_jobs where id=:id", {"id": first.job_id}
    ).iloc[0]["status"]
    assert status == "completed", "a same-window conflict must never revert a completed job"
    assert _count_inbox_sync() == 1


@requires_db
def test_db_enqueue_job_on_conflict_does_not_revert_terminal(scratch_db):
    """Repo-level proof of enqueue_job's ON CONFLICT (idempotency_key) DO
    UPDATE contract: re-enqueueing a key whose job is already terminal
    returns the same id and never resets status to pending."""
    from db_client import transaction
    from repositories.worker_job_repo import enqueue_job

    key = "inbox.sync:conflict-test"
    with transaction() as conn:
        first_id = enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id="primary_mailbox",
            payload={},
            idempotency_key=key,
            actor="system:inbox-scheduler",
        )
    db_client.execute("update worker_jobs set status='failed', last_error='x' where id=:id", {"id": first_id})

    with transaction() as conn:
        second_id = enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id="primary_mailbox",
            payload={},
            idempotency_key=key,
            actor="system:inbox-scheduler",
        )

    assert second_id == first_id
    status = db_client.read_df(
        "select status from worker_jobs where id=:id", {"id": first_id}
    ).iloc[0]["status"]
    assert status == "failed", "ON CONFLICT DO UPDATE must not revert a failed job to pending"


@requires_db
def test_db_concurrent_producers_cannot_both_enqueue(scratch_db):
    """P1: two scheduled producers on separate PostgreSQL sessions must not
    both enqueue an active inbox.sync. Simulate producer A by holding the
    mailbox advisory lock on its own connection/transaction; producer B
    (request_scheduled_inbox_sync, via the db_client engine = a separate
    session) must then skip and enqueue nothing. Releasing A's lock lets a
    later run enqueue."""
    from sqlalchemy import create_engine, text

    from application.inbox.commands import _SYNC_LOCK_NAME

    other = create_engine(MIGRATION_TEST_DATABASE_URL, future=True)
    conn = other.connect()
    trans = conn.begin()
    try:
        acquired = conn.execute(
            text("select pg_try_advisory_xact_lock(hashtext(:n))"), {"n": _SYNC_LOCK_NAME}
        ).scalar()
        assert acquired is True, "test setup could not take the advisory lock"

        # Separate session cannot acquire the same xact lock -> skip.
        result = commands.request_scheduled_inbox_sync(now=0.0)
        assert result.status == "skipped"
        assert "lock held" in result.reason
        assert _count_inbox_sync() == 0, "a concurrent producer must not enqueue while the lock is held"
    finally:
        trans.rollback()  # releases the advisory lock
        conn.close()
        other.dispose()

    # Lock released: the producer proceeds normally.
    resumed = commands.request_scheduled_inbox_sync(now=0.0)
    assert resumed.status == "queued"
    assert _count_inbox_sync() == 1
