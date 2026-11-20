"""Phase 10B: GET /api/v1/loads/stages - must resolve the Import-vs-other
service_flow "Returning Empty" column rule via services/dispatch_stages.py,
never duplicated in the Next.js client (CLAUDE.md constraint)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def test_import_service_flow_includes_returning_empty(client: TestClient):
    r = client.get("/api/v1/loads/stages?service_flow=Import")
    assert r.status_code == 200
    assert r.json()["stages"] == [
        "Ready to Dispatch",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Returning Empty",
        "Completed",
    ]


def test_export_service_flow_excludes_returning_empty(client: TestClient):
    r = client.get("/api/v1/loads/stages?service_flow=Export")
    assert r.status_code == 200
    assert "Returning Empty" not in r.json()["stages"]


def test_missing_service_flow_defaults_without_error(client: TestClient):
    r = client.get("/api/v1/loads/stages")
    assert r.status_code == 200
    assert isinstance(r.json()["stages"], list)
    assert r.json()["stages"]


def test_stages_route_is_not_shadowed_by_load_id_path(client: TestClient, monkeypatch):
    """Regression guard: /stages must resolve as the static stages route,
    never as GET /{load_id} with load_id="stages" (which would 422)."""
    from api.routers import loads as router_module

    monkeypatch.setattr(router_module, "get_load", lambda load_id: (_ for _ in ()).throw(AssertionError("should not be called")))

    r = client.get("/api/v1/loads/stages")
    assert r.status_code == 200
