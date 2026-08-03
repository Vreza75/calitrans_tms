from __future__ import annotations

import inspect

import pandas as pd

from pages_app import operations_inbox
from services import email_client, operations_attachment_service
from services.email_parser import parse_email_text


REQUEST_488_BODY = """Good afternoon,

Please arrange the attached local outbound transfer from our Katy warehouse
to the Gulf Consolidation Hub on Thursday, August 6. This load is moving to
a local forwarder only; it does not enter a port terminal and does not
require a PIN.

There are 18 wrapped pallets. Delivery appointment is 2:30 PM. Please reply
with the assigned driver and truck number.

Thank you,
Elena Ramirez
Outbound Logistics Manager
Katy Specialty Foods
(713) 555-0187"""


def test_request_488_partial_new_order_shows_order_draft_review() -> None:
    record = {
        "id": 488,
        "source_subject": "[TMS-TEST]-LOCAL-Export",
        "request_type": "New Booking",
        "request_type_clean": "New Booking",
        "work_queue": "New Orders",
        "dispatcher_queue": "New Orders",
    }
    parsed = {
        "TYPE": "Local Export",
        "Customer": "Katy Specialty Foods",
        "Booking Number": "",
        "Container Number": "",
    }

    assert operations_inbox.should_show_order_draft_review(
        record,
        parsed,
        attachments=[],
        pending_draft={},
        body=REQUEST_488_BODY,
    )


def test_request_488_parses_local_export_and_customer_exactly() -> None:
    parsed = parse_email_text(
        "[TMS-TEST]-LOCAL-Export",
        REQUEST_488_BODY,
        "Victor Reza <vreza75@gmail.com>",
    )

    assert parsed["TYPE"] == "Local Export"
    assert parsed["Customer"] == "Katy Specialty Foods"
    assert parsed["Contact Title"] == "Outbound Logistics Manager"


def test_missing_identifiers_or_attachment_do_not_hide_new_order_draft() -> None:
    record = {"request_type": "New Booking", "work_queue": "New Orders"}
    for parsed in (
        {"TYPE": "Local Import", "Customer": "Costco"},
        {"TYPE": "Local Export"},
        {"TYPE": "Import", "Booking Number": "IMP-260801"},
        {"TYPE": "Export", "Container Number": "CMAU2468101"},
    ):
        assert operations_inbox.should_show_order_draft_review(
            record,
            parsed,
            attachments=[],
            pending_draft={},
            body="Please arrange this new order.",
        )


def test_existing_update_does_not_become_new_order_review() -> None:
    assert not operations_inbox.should_show_order_draft_review(
        {
            "request_type": "Booking Update",
            "work_queue": "Existing Loads",
            "dispatcher_queue": "Existing Load Updates",
        },
        {"TYPE": "Export", "Booking Number": "LSP-EXP-080426"},
        attachments=[{"filename": "updated.pdf"}],
        pending_draft={},
        body="Please update the existing booking.",
    )


def test_shared_projection_prefers_pending_then_reconciled_then_document_then_email_then_hints() -> None:
    projected = operations_inbox.build_order_draft_projection(
        record={
            "customer_hint": "Hint Customer",
            "booking_hint": "HINT-BOOKING",
            "container_hint": "CMAU0000000",
        },
        parsed_data={
            "Customer": "Reconciled Customer",
            "Booking Number": "PARSED-BOOKING",
            "TYPE": "Export",
        },
        pending_draft={
            "customer": "Dispatcher Reviewed Customer",
            "service_flow": "Local Export",
        },
        attachment_fields={
            "Booking Number": "DOC-BOOKING",
            "Container Number": "CMAU2468101",
            "Size": "40HC",
        },
        email_fields={"Reference Number": "EMAIL-REF"},
        tokens={"reference_number": "TOKEN-REF"},
    )

    assert projected["Customer"] == "Dispatcher Reviewed Customer"
    assert projected["TYPE"] == "Local Export"
    assert projected["Booking Number"] == "PARSED-BOOKING"
    assert projected["Container Number"] == "CMAU2468101"
    assert projected["Size"] == "40HC"
    assert projected["Reference Number"] == "EMAIL-REF"


def test_queue_sort_defaults_received_descending_and_preserves_counts() -> None:
    frame = pd.DataFrame(
        [
            {"id": 1, "source_received_at": "2026-07-27T10:00:00Z", "customer_hint": "zeta"},
            {"id": 2, "source_received_at": "2026-07-28T10:00:00Z", "customer_hint": "Alpha"},
        ]
    )
    before = len(frame)
    sorted_frame = operations_inbox.sort_operations_queue(
        frame,
        sort_by="Received",
        direction="Descending",
    )

    assert sorted_frame["id"].tolist() == [2, 1]
    assert len(sorted_frame) == before


def test_queue_text_sort_is_case_insensitive_and_places_blanks_last() -> None:
    frame = pd.DataFrame(
        [
            {"id": 1, "customer_hint": "zeta", "booking_hint": ""},
            {"id": 2, "customer_hint": "Alpha", "booking_hint": "B-200"},
            {"id": 3, "customer_hint": "beta", "booking_hint": "A-100"},
        ]
    )

    ascending = operations_inbox.sort_operations_queue(
        frame,
        sort_by="Customer",
        direction="Ascending",
    )
    booking_desc = operations_inbox.sort_operations_queue(
        frame,
        sort_by="Booking",
        direction="Descending",
    )

    assert ascending["id"].tolist() == [2, 3, 1]
    assert booking_desc["id"].tolist() == [2, 3, 1]


def test_queue_blank_display_uses_em_dash_not_bullet() -> None:
    assert operations_inbox.queue_display_value("") == "—"
    assert operations_inbox.queue_display_value("•") == "—"
    assert operations_inbox.queue_display_value("LSP-EXP-080426") == "LSP-EXP-080426"


def test_operations_sync_enables_attachments_by_default() -> None:
    source = inspect.getsource(email_client.fetch_operations_email_sync)
    assert '_get_bool_setting("OPERATIONS_SYNC_ATTACHMENTS_ENABLED", True)' in source
    test_sync_source = inspect.getsource(email_client._fetch_test_sync_messages)
    assert "include_attachments=True" in test_sync_source


def test_queue_render_uses_bordered_rows_and_stable_open_id() -> None:
    source = inspect.getsource(operations_inbox.render_operations_inbox)
    assert 'key="operations_queue_header"' in source
    assert 'key=f"operations_queue_row_{work_item_id}"' in source
    assert 'key=f"open_work_item_{work_item_id}"' in source
    assert "queue_display_value(" in source


def test_same_filename_with_different_content_is_backfilled(monkeypatch) -> None:
    existing = {
        "id": 489,
        "parsed_data": {
            "_operations_attachments": [
                {
                    "filename": "dispatch_order.pdf",
                    "file_path": "old.pdf",
                    "content_sha256": "old-hash",
                }
            ]
        },
    }
    monkeypatch.setattr(
        operations_attachment_service,
        "save_operations_attachment",
        lambda **kwargs: {
            "filename": kwargs["filename"],
            "file_path": "new.pdf",
            "content_sha256": "new-hash",
            "parsed_data": {},
        },
    )
    monkeypatch.setattr(operations_attachment_service, "execute", lambda *args, **kwargs: None)

    saved = operations_attachment_service.backfill_operations_email_attachments(
        existing_record=existing,
        email_item={
            "attachments": [
                {
                    "filename": "dispatch_order.pdf",
                    "content": b"updated-content",
                    "content_type": "application/pdf",
                }
            ]
        },
        message_id="update-message",
    )

    assert saved == 1


def test_source_documents_distinguish_current_message_from_prior_conversation() -> None:
    current_record = {
        "id": 489,
        "source_message_id": "update-message",
        "source_subject": "[TMS-TEST]-Export",
        "source_received_at": "2026-07-28T18:54:14Z",
    }
    current_parsed = {
        "_operations_attachments": [
            {"filename": "updated.pdf", "file_path": "updated.pdf"}
        ]
    }
    timeline = pd.DataFrame(
        [
            {
                "id": 477,
                "source_message_id": "original-message",
                "source_subject": "New booking",
                "source_received_at": "2026-07-14T10:00:00Z",
                "parsed_data": {
                    "_operations_attachments": [
                        {"filename": "original.pdf", "file_path": "original.pdf"}
                    ]
                },
            },
            {
                **current_record,
                "parsed_data": current_parsed,
            },
        ]
    )

    groups = operations_attachment_service.group_operations_source_documents(
        current_record,
        current_parsed,
        timeline,
    )

    assert [item["filename"] for item in groups["current"]] == ["updated.pdf"]
    assert [item["filename"] for item in groups["prior"]] == ["original.pdf"]
    assert groups["current"][0]["source_message_id"] == "update-message"


def test_update_without_attachment_retains_prior_documents() -> None:
    groups = operations_attachment_service.group_operations_source_documents(
        {
            "id": 489,
            "source_message_id": "update-message",
            "source_subject": "Update",
        },
        {},
        pd.DataFrame(
            [
                {
                    "id": 477,
                    "source_message_id": "original-message",
                    "source_subject": "Original",
                    "parsed_data": {
                        "_operations_attachments": [
                            {"filename": "original.pdf", "file_path": "original.pdf"}
                        ]
                    },
                }
            ]
        ),
    )

    assert groups["current"] == []
    assert [item["filename"] for item in groups["prior"]] == ["original.pdf"]
