"""Phase 7 closure (STEP 5/10): application/jobs/queries.py tests.
Everything DB is mocked - these are wiring tests, not persistence tests
(that's tests/test_worker_job_repo.py and tests/test_outbox_repo.py).
"""
from __future__ import annotations

from unittest import mock

import pytest

from application.exceptions import NotFoundError
from application.jobs.queries import get_worker_job_status, get_worker_runtime_status


def test_get_worker_job_status_maps_safe_fields_only():
    conn = mock.MagicMock()
    job = {
        "id": 1,
        "job_type": "inbox.sync",
        "status": "completed",
        "attempt_count": 1,
        "created_at": "2026-08-13",
        "claimed_at": None,
        "completed_at": "2026-08-13",
        "last_error": None,
        "payload": {"secret": "should never reach the model"},
    }
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.get_job", return_value=job
    ):
        transaction.return_value.__enter__.return_value = conn
        result = get_worker_job_status(1)

    assert result.id == 1
    assert result.status == "completed"
    assert not hasattr(result, "payload")


def test_get_worker_job_status_raises_not_found_for_missing_job():
    conn = mock.MagicMock()
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.get_job", return_value=None
    ):
        transaction.return_value.__enter__.return_value = conn
        with pytest.raises(NotFoundError):
            get_worker_job_status(999)


def test_get_worker_runtime_status_aggregates_both_queues():
    conn = mock.MagicMock()
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.worker_job_repo.count_by_status", return_value={"pending": 2}
    ), mock.patch("repositories.outbox_repo.count_by_status", return_value={"delivered": 5}):
        transaction.return_value.__enter__.return_value = conn
        result = get_worker_runtime_status()

    assert result.worker_jobs_by_status == {"pending": 2}
    assert result.outbox_events_by_status == {"delivered": 5}
    assert result.checked_at is not None
