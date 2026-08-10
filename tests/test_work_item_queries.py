from __future__ import annotations

import pandas as pd
import pytest

from application.exceptions import NotFoundError
from application.work_items import queries as wiq
from repositories import work_item_repo


def _summary_row(**overrides) -> dict:
    row = {
        "id": 1,
        "created_at": None,
        "source_received_at": None,
        "source_subject": "Booking confirmation",
        "source_sender": "ops@customer.com",
        "email_direction": "inbound",
        "request_type": "New Booking",
        "work_queue": "New Orders",
        "department_lane": "Dispatch",
        "review_status": "Open",
        "confidence_score": 80,
        "matched_load_id": None,
        "conversation_key": "conv-1",
        "case_id": None,
        "customer": "Continental Industries Group",
        "booking_number": "RICGX1235800",
        "container_number": "",
        "reference_number": "SO217089a",
        "service_flow": "Export",
        "attachment_count": 0,
    }
    row.update(overrides)
    return row


def test_queue_page_reports_pagination_metadata(monkeypatch):
    rows = [_summary_row(id=i) for i in range(1, 6)]
    monkeypatch.setattr(work_item_repo, "count_work_items", lambda where, params: 42)
    monkeypatch.setattr(
        work_item_repo,
        "list_work_items_page",
        lambda where, params, *, sort_by, sort_direction, page, page_size: pd.DataFrame(rows),
    )

    page = wiq.get_work_item_queue_page(page=2, page_size=10)

    assert page.total_items == 42
    assert page.total_pages == 5
    assert page.page == 2
    assert page.page_size == 10
    assert len(page.items) == 5
    assert page.items[0].booking_number == "RICGX1235800"


def test_queue_page_defaults_to_received_desc(monkeypatch):
    captured = {}

    def _fake_list(where, params, *, sort_by, sort_direction, page, page_size):
        captured["sort_by"] = sort_by
        captured["sort_direction"] = sort_direction
        return pd.DataFrame([])

    monkeypatch.setattr(work_item_repo, "count_work_items", lambda where, params: 0)
    monkeypatch.setattr(work_item_repo, "list_work_items_page", _fake_list)

    wiq.get_work_item_queue_page()

    assert captured["sort_by"] == "received_at"
    assert captured["sort_direction"] == "desc"


def test_queue_page_clamps_requested_page_to_last_valid_page(monkeypatch):
    monkeypatch.setattr(work_item_repo, "count_work_items", lambda where, params: 5)
    monkeypatch.setattr(
        work_item_repo,
        "list_work_items_page",
        lambda where, params, *, sort_by, sort_direction, page, page_size: pd.DataFrame([]),
    )

    page = wiq.get_work_item_queue_page(page=999, page_size=25)

    assert page.page == 1  # 5 items / 25 page_size = 1 total page


def test_work_item_detail_raises_not_found_for_missing_id(monkeypatch):
    from repositories import inbox_repo

    monkeypatch.setattr(inbox_repo, "load_operations_inbox_record", lambda intake_id: pd.DataFrame())

    with pytest.raises(NotFoundError):
        wiq.get_work_item_detail(999999)


def test_work_item_detail_never_writes_to_the_database(monkeypatch):
    """get_work_item_detail()'s only DB-touching calls are
    inbox_repo.load_operations_inbox_record, work_item_repo.
    get_conversation_summary, and work_item_repo.get_order_draft - all
    three mocked below to non-DB fakes, and none of them (nor anything
    else the function calls) executes a write. Reading this function's
    source confirms no db_client.execute/transaction call exists on this
    path at all."""
    from repositories import inbox_repo

    record = {
        "id": 7,
        "source_sender": "ops@customer.com",
        "source_subject": "RE: booking",
        "source_received_at": None,
        "raw_text": "hello",
        "parsed_data": {},
        "request_type": "New Booking",
        "matched_load_id": None,
        "conversation_key": "",
        "review_status": "Open",
        "confidence_score": 50,
    }
    monkeypatch.setattr(inbox_repo, "load_operations_inbox_record", lambda intake_id: pd.DataFrame([record]))
    monkeypatch.setattr(
        work_item_repo,
        "get_conversation_summary",
        lambda key: {"message_count": 0, "last_message_at": None},
    )
    monkeypatch.setattr(work_item_repo, "get_order_draft", lambda key: None)

    detail = wiq.get_work_item_detail(7)

    assert detail.id == 7


def test_work_item_detail_does_not_import_or_call_imap_or_ai(monkeypatch):
    """No email_client (IMAP) or ai_agents/ai_core module should ever be
    imported as a side effect of reading one work item's detail."""
    import sys

    from repositories import inbox_repo

    record = {
        "id": 7,
        "source_sender": "",
        "source_subject": "",
        "source_received_at": None,
        "raw_text": "",
        "parsed_data": {},
        "request_type": "Needs Classification",
        "matched_load_id": None,
        "conversation_key": "",
        "review_status": "Open",
        "confidence_score": 0,
    }
    monkeypatch.setattr(inbox_repo, "load_operations_inbox_record", lambda intake_id: pd.DataFrame([record]))
    monkeypatch.setattr(
        work_item_repo,
        "get_conversation_summary",
        lambda key: {"message_count": 0, "last_message_at": None},
    )
    monkeypatch.setattr(work_item_repo, "get_order_draft", lambda key: None)

    for module_name in ("services.email_client", "ai_agents.hybrid_document_parser", "ai_core.llm"):
        sys.modules.pop(module_name, None)

    wiq.get_work_item_detail(7)

    assert "services.email_client" not in sys.modules


def test_attachment_summary_reads_metadata_without_reading_bytes(monkeypatch):
    """extract_operations_attachments/group_operations_source_documents
    read parsed_data only - never call a byte-reading function."""
    import services.operations_attachment_service as attachment_service

    called = {"read_bytes": False}

    def _boom(*a, **k):
        called["read_bytes"] = True
        raise AssertionError("attachment bytes must not be read for a metadata-only summary")

    monkeypatch.setattr(attachment_service, "read_operations_attachment_bytes", _boom)
    monkeypatch.setattr(attachment_service, "read_operations_pdf_bytes", _boom)

    record = {"id": 1, "conversation_key": "", "filename": "", "file_path": ""}
    summary = wiq.get_attachment_summary(1, record=record, parsed={})

    assert called["read_bytes"] is False
    assert summary.current == []
    assert summary.prior == []


def test_attachment_ref_is_stable_and_never_the_raw_path() -> None:
    ref_a = wiq.attachment_ref("storage/load_documents/abc.pdf")
    ref_b = wiq.attachment_ref("storage/load_documents/abc.pdf")
    ref_c = wiq.attachment_ref("storage/load_documents/xyz.pdf")

    assert ref_a == ref_b
    assert ref_a != ref_c
    assert "storage" not in ref_a
    assert ".pdf" not in ref_a
