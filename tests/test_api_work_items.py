from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from application.exceptions import NotFoundError, ValidationError
from application.work_items.models import (
    AttachmentMeta,
    AttachmentSummary,
    CommunicationSummary,
    FilterMeta,
    OrderDraftSummary,
    SortMeta,
    WorkItemDetail,
    WorkItemPage,
    WorkItemSummary,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _sample_summary(**overrides) -> WorkItemSummary:
    fields = dict(
        id=1,
        created_at=None,
        source_received_at=None,
        source_subject="Booking confirmation",
        source_sender="ops@customer.com",
        email_direction="inbound",
        request_type="New Booking",
        work_queue="New Orders",
        department_lane="Dispatch",
        review_status="Open",
        confidence_score=80,
        matched_load_id=None,
        conversation_key="conv-1",
        case_id=None,
        customer="Continental Industries Group",
        booking_number="RICGX1235800",
        container_number="",
        reference_number="SO217089a",
        service_flow="Export",
        attachment_count=0,
    )
    fields.update(overrides)
    return WorkItemSummary(**fields)


def _sample_detail(**overrides) -> WorkItemDetail:
    fields = dict(
        id=1,
        source_sender="ops@customer.com",
        source_subject="Booking confirmation",
        source_received_at=None,
        original_email_body="Please see attached booking.",
        parsed_data={"Customer": "Continental Industries Group"},
        request_type="New Booking",
        matched_load_id=None,
        conversation_key="conv-1",
        review_status="Open",
        confidence_score=80,
        attachments=AttachmentSummary(current=[], prior=[]),
        communication_summary=CommunicationSummary(message_count=1, last_message_at=None),
        order_draft=OrderDraftSummary(exists=False),
        allowed_actions=["create_load", "close_work_item"],
    )
    fields.update(overrides)
    return WorkItemDetail(**fields)


def test_health_v1(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_legacy_health_still_works(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200


def test_list_work_items_returns_paginated_shape(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    page = WorkItemPage(
        items=[_sample_summary()],
        page=1,
        page_size=25,
        total_items=1,
        total_pages=1,
        sort=SortMeta(sort_by="received_at", sort_direction="desc"),
        filters=FilterMeta(),
    )
    monkeypatch.setattr(router_module, "get_work_item_queue_page", lambda **kwargs: page)

    r = client.get("/api/v1/work-items")

    assert r.status_code == 200
    body = r.json()
    assert body["total_items"] == 1
    assert body["items"][0]["booking_number"] == "RICGX1235800"
    assert body["sort"]["sort_by"] == "received_at"


def test_get_work_item_detail_returns_200_for_existing_item(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    monkeypatch.setattr(router_module, "get_work_item_detail", lambda work_item_id: _sample_detail())

    r = client.get("/api/v1/work-items/1")

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 1
    assert body["allowed_actions"] == ["create_load", "close_work_item"]


def test_get_work_item_detail_returns_404_for_missing_item(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    def _raise(work_item_id: int):
        raise NotFoundError(f"Work item {work_item_id} not found.")

    monkeypatch.setattr(router_module, "get_work_item_detail", _raise)

    r = client.get("/api/v1/work-items/999999")

    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_attachments_response_never_includes_a_raw_file_path(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    summary = AttachmentSummary(
        current=[
            AttachmentMeta(
                filename="booking.pdf",
                file_path="/srv/calitrans/storage/load_documents/secret_internal_path.pdf",
                content_type="application/pdf",
                is_pdf=True,
                attachment_ref="abc123",
            )
        ],
        prior=[],
    )
    monkeypatch.setattr(router_module, "get_attachments_for_work_item", lambda work_item_id: summary)

    r = client.get("/api/v1/work-items/1/attachments")

    assert r.status_code == 200
    raw_body = r.text
    assert "secret_internal_path" not in raw_body
    assert "/srv/" not in raw_body
    assert r.json()["current"][0]["attachment_ref"] == "abc123"


def test_close_work_item_endpoint_validates_json_body(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module
    from application.work_items.commands import CommandResult

    monkeypatch.setattr(
        router_module.work_item_commands,
        "close_work_item",
        lambda work_item_id, actor, reason: CommandResult(ok=True, work_item_id=work_item_id, detail="Closed"),
    )

    r = client.post("/api/v1/work-items/1/close", json={"reason": "handled"})

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_create_load_returns_422_on_validation_error(client: TestClient, monkeypatch) -> None:
    from api.routers import work_items as router_module

    def _raise(work_item_id, approved_fields):
        raise ValidationError("Booking Number or Reference Number is required to create a load.")

    monkeypatch.setattr(router_module, "create_load_from_work_item", _raise)

    r = client.post("/api/v1/work-items/1/create-load", json={"approved_fields": {}})

    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_unhandled_exception_does_not_leak_internal_detail(monkeypatch) -> None:
    from api.routers import work_items as router_module

    def _boom(work_item_id):
        raise RuntimeError("connection to postgresql://user:hunter2@db.internal:5432/prod failed")

    monkeypatch.setattr(router_module, "get_work_item_detail", _boom)

    # raise_server_exceptions=False: let the registered Exception handler
    # produce its real HTTP response instead of TestClient re-raising the
    # exception for debugging (its default behavior) - this is what an
    # actual client over the network would receive.
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/work-items/1")

    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error"
    assert "hunter2" not in r.text
    assert "postgresql://" not in r.text


def test_loads_list_endpoint(client: TestClient, monkeypatch) -> None:
    from api.routers import loads as loads_router
    from application.loads.models import LoadSummary

    monkeypatch.setattr(
        loads_router,
        "list_loads",
        lambda status=None, limit=100: [
            LoadSummary(
                id=1,
                type="Export",
                booking_number="RICGX1235800",
                reference_number="",
                container_number="",
                customer="Continental Industries Group",
                status="New",
                driver_name="",
                truck_assigned="",
                updated_at=None,
            )
        ],
    )

    r = client.get("/api/v1/loads")

    assert r.status_code == 200
    assert r.json()[0]["booking_number"] == "RICGX1235800"
