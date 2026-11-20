"""Phase 10B: GET /api/v1/drivers/active - mirrors tests/test_api_loads_search.py's
pattern (dev-mode TestClient, monkeypatch the router module's query function,
explicit role-gating proof)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def test_list_active_drivers_returns_roster(client: TestClient, monkeypatch):
    from api.routers import drivers as router_module
    from application.drivers.models import ActiveDriver

    monkeypatch.setattr(
        router_module,
        "list_active_drivers",
        lambda: [ActiveDriver(driver_name="Joe Driver", phone="5551234567", truck_number="T-100")],
    )

    r = client.get("/api/v1/drivers/active")

    assert r.status_code == 200
    body = r.json()
    assert body == [{"driver_name": "Joe Driver", "phone": "5551234567", "truck_number": "T-100"}]


def test_list_active_drivers_empty_roster_returns_empty_list(client: TestClient, monkeypatch):
    from api.routers import drivers as router_module

    monkeypatch.setattr(router_module, "list_active_drivers", lambda: [])

    r = client.get("/api/v1/drivers/active")

    assert r.status_code == 200
    assert r.json() == []


def test_list_active_drivers_unauthenticated_returns_401(monkeypatch):
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("API_AUTH_TOKENS", raising=False)
    client = TestClient(app)

    r = client.get("/api/v1/drivers/active")

    assert r.status_code == 401
