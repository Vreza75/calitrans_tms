"""Phase 10C: Load Workspace API tests - mirrors tests/test_api_drivers.py's
and tests/test_api_load_stages.py's pattern (dev-mode TestClient, monkeypatch
the router module's imported command, explicit role/permission-gating proof).
Every endpoint here is a thin wrapper over an already-tested application
command - these tests prove the wrapper's routing/schema/status-code
behavior, not the command's own business logic (covered elsewhere:
tests/test_load_commands_authorization.py, tests/test_application_loads_commands_error_sanitization.py).
"""
from __future__ import annotations

import io
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from application.exceptions import AuthorizationError, ValidationError
from application.loads.models import LoadCommandResult


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def _accounting_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        json.dumps([{"token": "acct-token", "actor": "accounting@calitranscorp.com", "role": "accounting"}]),
    )
    return TestClient(app)


ACCOUNTING_AUTH = {"Authorization": "Bearer acct-token"}


# ---------------------------------------------------------------------------
# PUT /loads/{id} - update_load_fields
# ---------------------------------------------------------------------------


def test_update_load_fields_endpoint_returns_result(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    monkeypatch.setattr(
        router_module, "update_load_fields", lambda **kwargs: LoadCommandResult(ok=True, load_id=1, status="Active")
    )

    r = client.put("/api/v1/loads/1", json={"updates": {"Customer": "Acme"}})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "load_id": 1, "status": "Active", "reason": ""}


def test_update_load_fields_endpoint_rejects_empty_updates(client: TestClient, monkeypatch):
    """Not mocked - exercises the real command's existing ValidationError
    for an empty updates dict, proving the 422 mapping end-to-end."""
    r = client.put("/api/v1/loads/1", json={"updates": {}})

    assert r.status_code == 422


def test_update_load_fields_endpoint_requires_mutate_operations_role(monkeypatch):
    client = _accounting_client(monkeypatch)
    r = client.put("/api/v1/loads/1", json={"updates": {"Customer": "Acme"}}, headers=ACCOUNTING_AUTH)
    assert r.status_code == 403


def test_update_load_fields_endpoint_unauthenticated_returns_401(monkeypatch):
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    client = TestClient(app)
    r = client.put("/api/v1/loads/1", json={"updates": {"Customer": "Acme"}})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /loads/{id}/notes - save_load_note
# ---------------------------------------------------------------------------


def test_save_load_note_endpoint_returns_result(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    monkeypatch.setattr(router_module, "save_load_note", lambda **kwargs: LoadCommandResult(ok=True, load_id=1))

    r = client.post("/api/v1/loads/1/notes", json={"note": "called customer"})

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_save_load_note_endpoint_requires_mutate_operations_role(monkeypatch):
    client = _accounting_client(monkeypatch)
    r = client.post("/api/v1/loads/1/notes", json={"note": "x"}, headers=ACCOUNTING_AUTH)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /loads/{id}/mark-missing-info, /verify-booking, /cancel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,command_name,expected_status",
    [
        ("mark-missing-info", "mark_load_missing_info", "Hold/Need Info"),
        ("verify-booking", "verify_load_booking", "Booking Verified"),
        ("cancel", "cancel_load", "Cancelled"),
    ],
)
def test_status_quick_action_endpoints_return_result(client: TestClient, monkeypatch, path, command_name, expected_status):
    from api.routers import loads as router_module

    monkeypatch.setattr(
        router_module, command_name, lambda **kwargs: LoadCommandResult(ok=True, load_id=1, status=expected_status)
    )

    r = client.post(f"/api/v1/loads/1/{path}", json={"note": "because"})

    assert r.status_code == 200
    assert r.json()["status"] == expected_status


@pytest.mark.parametrize("path", ["mark-missing-info", "verify-booking", "cancel"])
def test_status_quick_action_endpoints_require_mutate_operations_role(monkeypatch, path):
    client = _accounting_client(monkeypatch)
    r = client.post(f"/api/v1/loads/1/{path}", json={}, headers=ACCOUNTING_AUTH)
    assert r.status_code == 403


def test_status_quick_action_note_defaults_to_empty_string(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    captured = {}
    monkeypatch.setattr(
        router_module,
        "cancel_load",
        lambda **kwargs: (captured.update(kwargs), LoadCommandResult(ok=True, load_id=1, status="Cancelled"))[1],
    )

    r = client.post("/api/v1/loads/1/cancel", json={})

    assert r.status_code == 200
    assert captured["note"] == ""


def test_cancel_endpoint_surfaces_authorization_error_as_403_not_500(client: TestClient, monkeypatch):
    """Regression guard for the AuthorizationError -> 403 handler added
    in api/errors.py (Phase 10C) - before that handler existed, this
    would have fallen through to the generic 500 handler."""
    from api.routers import loads as router_module

    def _raise(**kwargs):
        raise AuthorizationError("Role 'dispatcher' does not have permission 'load:cancel'.")

    monkeypatch.setattr(router_module, "cancel_load", _raise)

    r = client.post("/api/v1/loads/1/cancel", json={})

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# POST /loads/{id}/communications - log_dispatch_communication
# ---------------------------------------------------------------------------


def test_log_communication_endpoint_returns_ok(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    captured = {}
    monkeypatch.setattr(router_module, "log_dispatch_communication", lambda **kwargs: captured.update(kwargs))

    r = client.post(
        "/api/v1/loads/1/communications",
        json={"message_type": "note", "direction": "internal", "recipient": "", "message_body": "called customer"},
    )

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert captured["provider"] == "internal"  # fixed server-side, not client-settable
    assert captured["message_body"] == "called customer"


def test_log_communication_endpoint_requires_mutate_operations_role(monkeypatch):
    client = _accounting_client(monkeypatch)
    r = client.post(
        "/api/v1/loads/1/communications",
        json={"message_type": "note", "direction": "internal", "message_body": "x"},
        headers=ACCOUNTING_AUTH,
    )
    assert r.status_code == 403


def test_log_communication_endpoint_requires_message_body(client: TestClient):
    r = client.post(
        "/api/v1/loads/1/communications",
        json={"message_type": "note", "direction": "internal"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /loads/{id}/documents - attach_load_document
# ---------------------------------------------------------------------------


def test_attach_document_endpoint_returns_pending(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    monkeypatch.setattr(router_module, "attach_load_document", lambda **kwargs: 42)

    r = client.post("/api/v1/loads/1/documents", files={"file": ("rate.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "document_id": 42, "status": "pending"}


def test_attach_document_endpoint_passes_filename_through_the_sync_adapter(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    captured = {}

    def _fake_attach(*, actor, load_id, uploaded_file, source):
        captured["name"] = uploaded_file.name
        captured["bytes"] = uploaded_file.read()
        captured["source"] = source
        return 1

    monkeypatch.setattr(router_module, "attach_load_document", _fake_attach)

    r = client.post("/api/v1/loads/1/documents", files={"file": ("rate.pdf", io.BytesIO(b"hello world"), "application/pdf")})

    assert r.status_code == 200
    assert captured["name"] == "rate.pdf"
    assert captured["bytes"] == b"hello world"
    assert captured["source"] == "web"


def test_attach_document_endpoint_allows_accounting(monkeypatch):
    """DOCUMENT_MUTATE (not MUTATE_OPERATIONS) gates this route -
    Permission.DOCUMENT_ATTACH is the one Phase 5B mutation permission
    Accounting holds."""
    client = _accounting_client(monkeypatch)
    from api.routers import loads as router_module

    monkeypatch.setattr(router_module, "attach_load_document", lambda **kwargs: 7)

    r = client.post(
        "/api/v1/loads/1/documents",
        files={"file": ("invoice.pdf", io.BytesIO(b"x"), "application/pdf")},
        headers=ACCOUNTING_AUTH,
    )

    assert r.status_code == 200
    assert r.json()["document_id"] == 7


def test_attach_document_endpoint_unauthenticated_returns_401(monkeypatch):
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    client = TestClient(app)
    r = client.post("/api/v1/loads/1/documents", files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 401
