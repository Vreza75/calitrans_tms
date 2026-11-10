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
    monkeypatch.setattr(worker_job_repo, "has_active_job", lambda conn, job_type: False)
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


# --------------------------------------------------------------------------
# Real-PostgreSQL tests - opt-in, disposable database only
# --------------------------------------------------------------------------

requires_db = pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
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
