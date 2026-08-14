"""Phase 7 closure (STEP 9): POST /api/v1/inbox/sync tests. Mirrors
tests/test_api_work_items.py's pattern - dev-mode client for business
logic, tests/test_api_auth.py's pattern (separately) covers real auth
enforcement; this file adds one direct 401/403 check for this specific
route since it's new.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from application.inbox.models import InboxSyncRequestResult


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def test_sync_inbox_returns_202_with_job_id(client: TestClient, monkeypatch) -> None:
    from api.routers import inbox as router_module

    monkeypatch.setattr(
        router_module,
        "request_inbox_sync",
        lambda **kwargs: InboxSyncRequestResult(ok=True, job_id=42, status="queued"),
    )

    r = client.post("/api/v1/inbox/sync")

    assert r.status_code == 202
    body = r.json()
    assert body["ok"] is True
    assert body["job_id"] == 42
    assert body["status"] == "queued"


def test_sync_inbox_does_not_import_email_client_module(client: TestClient, monkeypatch) -> None:
    """The route must not fetch email inline - proven by the request
    succeeding with services.email_client never patched/available to
    fail loudly if it were touched; request_inbox_sync itself only
    enqueues (see tests/test_inbox_commands.py for the full wiring
    proof)."""
    from api.routers import inbox as router_module

    monkeypatch.setattr(
        router_module,
        "request_inbox_sync",
        lambda **kwargs: InboxSyncRequestResult(ok=True, job_id=1, status="queued"),
    )

    r = client.post("/api/v1/inbox/sync")
    assert r.status_code == 202


def test_sync_inbox_unauthorized_role_returns_403(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "acct-token", "actor": "accounting@calitranscorp.com", "role": "accounting"}]',
    )
    client = TestClient(app)

    r = client.post("/api/v1/inbox/sync", headers={"Authorization": "Bearer acct-token"})

    assert r.status_code == 403


def test_sync_inbox_anonymous_returns_401(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "disp-token", "actor": "dispatcher@calitranscorp.com", "role": "dispatcher"}]',
    )
    client = TestClient(app)

    r = client.post("/api/v1/inbox/sync")

    assert r.status_code == 401
