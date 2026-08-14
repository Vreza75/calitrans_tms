"""Phase 7: workers/processor.py tests. Everything DB is mocked
(repositories.worker_job_repo functions, db_client.transaction) - these
tests are about the processor's own dispatch/retry/terminal-failure
logic, not about real persistence (that's tests/test_worker_job_repo.py,
including the PostgreSQL-gated concurrent-claim test).

JOB_HANDLERS starts empty (no job type has a handler yet - see workers/
processor.py's module docstring), so success/retry/failure-path tests
here register a fake handler via mock.patch.dict(..., clear=True) rather
than relying on any real handler existing.
"""
from __future__ import annotations

from unittest import mock

from workers import processor


def _fake_transaction(conn):
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_inbox_sync_handler_is_registered():
    from workers.inbox_handlers import handle_inbox_sync

    assert processor.JOB_HANDLERS.get("inbox.sync") is handle_inbox_sync


def test_process_one_returns_none_when_nothing_claimable():
    conn = mock.MagicMock()
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=None
    ):
        result = processor.process_one()
    assert result is None


def test_process_one_success_marks_completed():
    conn = mock.MagicMock()
    job = {
        "id": 1,
        "job_type": "test.job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": 0,
        "actor": "system:test",
    }
    fake_handler = mock.Mock(return_value=(True, ""))
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_completed") as mark_completed, mock.patch.dict(
        processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True
    ):
        result = processor.process_one()

    assert result == {"id": 1, "job_type": "test.job", "outcome": "completed", "error": ""}
    fake_handler.assert_called_once_with({})
    mark_completed.assert_called_once_with(conn, 1)


def test_process_one_failure_below_max_attempts_retries():
    conn = mock.MagicMock()
    job = {
        "id": 2,
        "job_type": "test.job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": 1,
        "actor": None,
    }
    fake_handler = mock.Mock(return_value=(False, "transient failure"))
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_retry") as mark_retry, mock.patch.dict(
        processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True
    ):
        result = processor.process_one()

    assert result["outcome"] == "retrying"
    mark_retry.assert_called_once()
    assert mark_retry.call_args.args[1] == 2
    assert mark_retry.call_args.kwargs["error"] == "transient failure"


def test_process_one_failure_at_max_attempts_is_terminal():
    conn = mock.MagicMock()
    job = {
        "id": 3,
        "job_type": "test.job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": processor.MAX_ATTEMPTS - 1,
        "actor": None,
    }
    fake_handler = mock.Mock(return_value=(False, "still failing"))
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_failed") as mark_failed, mock.patch.dict(
        processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True
    ):
        result = processor.process_one()

    assert result["outcome"] == "failed"
    mark_failed.assert_called_once()
    assert mark_failed.call_args.args[1] == 3


def test_process_one_unknown_job_type_fails_immediately_without_calling_a_handler():
    conn = mock.MagicMock()
    job = {
        "id": 4,
        "job_type": "totally_unregistered_job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_failed") as mark_failed, mock.patch.dict(
        processor.JOB_HANDLERS, {}, clear=True
    ):
        result = processor.process_one()

    assert result["outcome"] == "failed"
    mark_failed.assert_called_once()
    assert mark_failed.call_args.args[1] == 4


def test_process_one_handler_exception_is_sanitized_not_leaked():
    """Regression guard mirroring outbox: worker_jobs.last_error must
    never contain credential material. Fake DSN/password only - never a
    real secret in a test."""
    conn = mock.MagicMock()
    job = {
        "id": 5,
        "job_type": "test.job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }
    boom = RuntimeError("connection to postgresql://user:hunter2@db.internal:5432/prod failed")
    fake_handler = mock.Mock(side_effect=boom)
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_retry") as mark_retry, mock.patch.dict(
        processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True
    ):
        result = processor.process_one()

    assert "hunter2" not in result["error"]
    mark_retry.assert_called_once()
    assert "hunter2" not in mark_retry.call_args.kwargs["error"]


def test_process_one_handler_returned_failure_string_is_also_sanitized():
    """A handler's own returned failure string is not an exception - it
    must be sanitized on the same choke point as the exception path.
    Fake credentials only - never a real secret in a test."""
    conn = mock.MagicMock()
    job = {
        "id": 6,
        "job_type": "test.job",
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }
    fake_error = "upstream error - Authorization: Bearer fake.jwt.token or api_key=FAKEKEY123"
    fake_handler = mock.Mock(return_value=(False, fake_error))
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.claim_next_job", return_value=job
    ), mock.patch("repositories.worker_job_repo.mark_retry") as mark_retry, mock.patch.dict(
        processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True
    ):
        result = processor.process_one()

    for secret in ("FAKEKEY123", "fake.jwt.token"):
        assert secret not in result["error"]
        assert secret not in mark_retry.call_args.kwargs["error"]


def test_process_pending_reclaims_stale_processing_before_claiming():
    conn = mock.MagicMock()
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.reclaim_stuck_processing", return_value=2
    ) as reclaim, mock.patch("repositories.worker_job_repo.claim_next_job", return_value=None):
        results = processor.process_pending()

    reclaim.assert_called_once_with(conn, older_than=processor.RECLAIM_STALE_AFTER)
    assert results == []


def test_process_pending_stops_when_nothing_left_to_claim():
    conn = mock.MagicMock()
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.reclaim_stuck_processing", return_value=0
    ), mock.patch("repositories.worker_job_repo.claim_next_job", return_value=None):
        results = processor.process_pending(max_jobs=10)

    assert results == []


def test_process_pending_respects_max_jobs_cap():
    conn = mock.MagicMock()
    jobs = [
        {
            "id": i,
            "job_type": "test.job",
            "aggregate_type": "test",
            "aggregate_id": "1",
            "payload": {},
            "attempt_count": 0,
            "actor": None,
        }
        for i in range(1, 6)
    ]
    fake_handler = mock.Mock(return_value=(True, ""))
    with mock.patch("workers.processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.worker_job_repo.reclaim_stuck_processing", return_value=0
    ), mock.patch("repositories.worker_job_repo.claim_next_job", side_effect=jobs), mock.patch(
        "repositories.worker_job_repo.mark_completed"
    ), mock.patch.dict(processor.JOB_HANDLERS, {"test.job": fake_handler}, clear=True):
        results = processor.process_pending(max_jobs=2)

    assert len(results) == 2
