"""Phase 7 closure (STEP 5/10): GET /api/v1/jobs/{job_id} and
GET /api/v1/worker/status tests.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from application.exceptions import NotFoundError
from application.jobs.models import WorkerJobStatus, WorkerRuntimeStatus


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def _status(**overrides) -> WorkerJobStatus:
    fields = dict(
        id=1,
        job_type="inbox.sync",
        status="pending",
        attempt_count=0,
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        claimed_at=None,
        completed_at=None,
        last_error=None,
    )
    fields.update(overrides)
    return WorkerJobStatus(**fields)


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "failed"])
def test_get_job_status_returns_each_lifecycle_state(client: TestClient, monkeypatch, status: str) -> None:
    from api.routers import jobs as router_module

    monkeypatch.setattr(router_module, "get_worker_job_status", lambda job_id: _status(status=status))

    r = client.get("/api/v1/jobs/1")

    assert r.status_code == 200
    assert r.json()["status"] == status


def test_get_job_status_returns_sanitized_error_field_as_is(client: TestClient, monkeypatch) -> None:
    """last_error is already sanitized before it's ever persisted
    (workers/processor.py routes every error through utils.
    error_sanitizer) - the API must pass it through unchanged, not
    re-process it."""
    from api.routers import jobs as router_module

    monkeypatch.setattr(
        router_module,
        "get_worker_job_status",
        lambda job_id: _status(status="failed", attempt_count=5, last_error="Twilio error (401): could not authenticate"),
    )

    r = client.get("/api/v1/jobs/1")

    body = r.json()
    assert body["last_error"] == "Twilio error (401): could not authenticate"
    assert body["attempt_count"] == 5


def test_get_job_status_returns_404_for_missing_job(client: TestClient, monkeypatch) -> None:
    from api.routers import jobs as router_module

    def _raise(job_id: int):
        raise NotFoundError(f"Job {job_id} not found.")

    monkeypatch.setattr(router_module, "get_worker_job_status", _raise)

    r = client.get("/api/v1/jobs/999999")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_get_job_status_never_exposes_a_payload_field(client: TestClient, monkeypatch) -> None:
    from api.routers import jobs as router_module

    monkeypatch.setattr(router_module, "get_worker_job_status", lambda job_id: _status())

    r = client.get("/api/v1/jobs/1")

    assert "payload" not in r.json()


def test_worker_status_returns_queue_depths(client: TestClient, monkeypatch) -> None:
    from api.routers import jobs as router_module

    monkeypatch.setattr(
        router_module,
        "get_worker_runtime_status",
        lambda: WorkerRuntimeStatus(
            worker_jobs_by_status={"pending": 3, "failed": 1},
            outbox_events_by_status={"delivered": 10},
            checked_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        ),
    )

    r = client.get("/api/v1/worker/status")

    assert r.status_code == 200
    body = r.json()
    assert body["worker_jobs_by_status"] == {"pending": 3, "failed": 1}
    assert body["outbox_events_by_status"] == {"delivered": 10}


def test_jobs_endpoints_unauthorized_role_returns_403(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "acct-token", "actor": "accounting@calitranscorp.com", "role": "accounting"}]',
    )
    client = TestClient(app)

    r = client.get("/api/v1/jobs/1", headers={"Authorization": "Bearer acct-token"})
    assert r.status_code == 403

    r = client.get("/api/v1/worker/status", headers={"Authorization": "Bearer acct-token"})
    assert r.status_code == 403


def test_jobs_endpoints_anonymous_returns_401(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "disp-token", "actor": "dispatcher@calitranscorp.com", "role": "dispatcher"}]',
    )
    client = TestClient(app)

    r = client.get("/api/v1/jobs/1")
    assert r.status_code == 401
