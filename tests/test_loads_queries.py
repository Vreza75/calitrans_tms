"""Phase 8: application/loads/queries.py new read-model functions.
Repository layer mocked - wiring/contract tests, not persistence tests
(that's tests/test_load_query_repo.py).
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from application.exceptions import NotFoundError
from application.loads.models import LoadFilters
from application.loads.queries import (
    get_load_communications,
    get_load_detail,
    get_load_documents,
    get_load_timeline,
    search_loads,
)


def _loads_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_search_loads_returns_paginated_typed_items():
    row = {
        "id": 1,
        "type": "Export",
        "booking_number": "BOOK001",
        "reference_number": "REF001",
        "container_number": "MSCU0000001",
        "customer": "Continental",
        "port": "Houston",
        "warehouse": "PBP",
        "status": "Active",
        "driver_name": "Juan Perez",
        "truck_assigned": "T-1",
        "delivery_need_date": "2026-08-15",
        "document_cutoff": "2026-08-10",
        "invoice_status": "Ready",
        "driver_pay_status": "Pending",
        "updated_at": "2026-08-13T00:00:00",
    }
    with mock.patch("repositories.load_query_repo.count_loads", return_value=1), mock.patch(
        "repositories.load_query_repo.list_loads_page", return_value=_loads_df([row])
    ), mock.patch(
        "repositories.load_query_repo.normalize_sort", return_value=("updated_at", "desc")
    ):
        result = search_loads(filters=LoadFilters(status="Active"), page=1, page_size=50)

    assert result.total_items == 1
    assert result.items[0].booking_number == "BOOK001"
    assert result.sort_by == "updated_at"


def test_search_loads_page_beyond_total_pages_is_clamped():
    with mock.patch("repositories.load_query_repo.count_loads", return_value=3), mock.patch(
        "repositories.load_query_repo.list_loads_page", return_value=_loads_df([])
    ) as list_page, mock.patch(
        "repositories.load_query_repo.normalize_sort", return_value=("updated_at", "desc")
    ):
        search_loads(page=999, page_size=50)

    assert list_page.call_args.kwargs["page"] == 1  # only 1 page exists for 3 items at page_size=50


def test_get_load_detail_returns_typed_detail():
    row = {
        "id": 1,
        "type": "Export",
        "load_id": "",
        "booking_number": "BOOK001",
        "reference_number": "",
        "container_number": "",
        "customer": "",
        "port": "",
        "warehouse": "",
        "address": "",
        "document_cutoff": None,
        "delivery_need_date": None,
        "load_date": None,
        "lfd": None,
        "status": "Active",
        "driver_name": "",
        "truck_assigned": "",
        "chassis": "",
        "size": "",
        "billing_notes": "",
        "dispatcher_notes": "",
        "invoice_status": "",
        "driver_pay_status": "",
        "closeout_stage": "",
        "steamship_line": "",
        "vessel_name": "",
        "terminal": "",
        "pickup_appointment": None,
        "delivery_appointment": None,
        "empty_return_location": "",
        "empty_return_date": None,
        "parent_booking_key": "",
        "container_sequence": None,
        "container_total": None,
        "created_at": None,
        "updated_at": None,
    }
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value=row):
        detail = get_load_detail(1)

    assert detail.id == 1
    assert detail.booking_number == "BOOK001"


def test_get_load_detail_raises_not_found():
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value=None):
        with pytest.raises(NotFoundError):
            get_load_detail(999999)


def test_get_load_timeline_raises_not_found_for_missing_load():
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value=None):
        with pytest.raises(NotFoundError):
            get_load_timeline(999999)


def test_get_load_timeline_returns_typed_events():
    events_df = pd.DataFrame(
        [{"event_type": "status_change", "title": "Active", "details": "assigned", "actor": "dispatcher", "created_at": "2026-08-13"}]
    )
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value={"id": 1}), mock.patch(
        "repositories.load_query_repo.count_load_timeline_events", return_value=1
    ), mock.patch("repositories.load_query_repo.list_load_timeline_page", return_value=events_df):
        result = get_load_timeline(1)

    assert result.total_items == 1
    assert result.items[0].event_type == "status_change"


def test_get_load_communications_raises_not_found_for_missing_load():
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value=None):
        with pytest.raises(NotFoundError):
            get_load_communications(999999)


def test_get_load_communications_returns_typed_records():
    comms_df = pd.DataFrame(
        [
            {
                "id": 1,
                "message_type": "driver_dispatch_sms",
                "direction": "outbound",
                "recipient": "+15551234567",
                "message_body": "hi",
                "sent_by": "system:inbox-worker",
                "provider": "twilio",
                "delivery_status": "delivered",
                "provider_message_id": "SM123",
                "created_at": "2026-08-13",
            }
        ]
    )
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value={"id": 1}), mock.patch(
        "repositories.load_query_repo.count_load_communications", return_value=1
    ), mock.patch("repositories.load_query_repo.list_load_communications_page", return_value=comms_df):
        result = get_load_communications(1)

    assert result.items[0].provider_message_id == "SM123"


def test_get_load_documents_raises_not_found_for_missing_load():
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value=None):
        with pytest.raises(NotFoundError):
            get_load_documents(999999)


def test_get_load_documents_returns_typed_metadata_without_file_path():
    rows = [
        {"id": 1, "document_type": "load_pdf", "filename": "rate.pdf", "source": "invoice", "status": "available", "created_at": "2026-08-13"}
    ]
    with mock.patch("repositories.load_query_repo.get_load_detail_row", return_value={"id": 1}), mock.patch(
        "repositories.document_repo.list_documents_for_load", return_value=rows
    ):
        documents = get_load_documents(1)

    assert documents[0].filename == "rate.pdf"
    assert not hasattr(documents[0], "file_path")
