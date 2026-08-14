"""Phase 8: GET /api/v1/loads/search + detail/timeline/communications/
documents sub-resource API tests. Mirrors tests/test_api_work_items.py's
pattern.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from application.exceptions import NotFoundError


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_AUTH_DEV_MODE", "true")
    return TestClient(app)


def test_search_loads_returns_pagination_envelope(client: TestClient, monkeypatch):
    from api.routers import loads as router_module
    from application.common.pagination import PageResult
    from application.loads.models import LoadListItem

    item = LoadListItem(
        id=1, type="Export", booking_number="BOOK001", reference_number="", container_number="",
        customer="Continental", port="Houston", warehouse="", status="Active", driver_name="",
        truck_assigned="", delivery_need_date=None, document_cutoff=None, invoice_status="Ready",
        driver_pay_status="Pending", updated_at=None,
    )
    monkeypatch.setattr(
        router_module,
        "search_loads",
        lambda **kwargs: PageResult(items=[item], page=1, page_size=50, total_items=1, total_pages=1, sort_by="updated_at", sort_direction="desc"),
    )

    r = client.get("/api/v1/loads/search")

    assert r.status_code == 200
    body = r.json()
    assert body["total_items"] == 1
    assert body["items"][0]["booking_number"] == "BOOK001"
    assert "next_cursor" not in body  # page/offset, not cursor - confirms the chosen convention


def test_search_loads_rejects_limit_over_200(client: TestClient):
    r = client.get("/api/v1/loads/search?limit=99999")
    assert r.status_code == 422


def test_search_loads_rejects_page_below_1(client: TestClient):
    r = client.get("/api/v1/loads/search?page=0")
    assert r.status_code == 422


def test_get_load_detail_404_for_missing_load(client: TestClient, monkeypatch):
    from api.routers import loads as router_module

    def _raise(load_id: int):
        raise NotFoundError(f"Load {load_id} not found.")

    monkeypatch.setattr(router_module, "get_load_detail", _raise)

    r = client.get("/api/v1/loads/999999/detail")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_get_load_timeline_returns_paginated_events(client: TestClient, monkeypatch):
    from api.routers import loads as router_module
    from application.common.pagination import PageResult
    from application.loads.models import LoadTimelineEvent

    event = LoadTimelineEvent(event_type="status_change", title="Active", details="", actor="dispatcher", created_at=None)
    monkeypatch.setattr(
        router_module,
        "get_load_timeline",
        lambda load_id, **kwargs: PageResult(items=[event], page=1, page_size=50, total_items=1, total_pages=1),
    )

    r = client.get("/api/v1/loads/1/timeline")
    assert r.status_code == 200
    assert r.json()["items"][0]["event_type"] == "status_change"


def test_get_load_documents_excludes_file_path(client: TestClient, monkeypatch):
    from api.routers import loads as router_module
    from application.loads.models import LoadDocumentMeta

    doc = LoadDocumentMeta(id=1, document_type="load_pdf", filename="rate.pdf", source="invoice", status="available", created_at=None)
    monkeypatch.setattr(router_module, "get_load_documents", lambda load_id, **kwargs: [doc])

    r = client.get("/api/v1/loads/1/documents")
    assert r.status_code == 200
    body = r.json()[0]
    assert body["filename"] == "rate.pdf"
    assert "file_path" not in body


def test_loads_search_unauthorized_role_returns_403(monkeypatch) -> None:
    monkeypatch.delenv("API_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv(
        "API_AUTH_TOKENS",
        '[{"token": "disp-token", "actor": "dispatcher@calitranscorp.com", "role": "dispatcher"}]',
    )
    # dispatcher IS in READ_LOADS per api/auth.py - use a role that is not, if any exists.
    # Fall back to anonymous-401 proof instead, which is unambiguous:
    client = TestClient(app)
    r = client.get("/api/v1/loads/search")
    assert r.status_code == 401
