from __future__ import annotations

import math

from application.common.pagination import PageResult, normalize_page, normalize_page_size
from application.exceptions import NotFoundError
from application.loads.models import (
    LoadCommunication,
    LoadDetail,
    LoadDocumentMeta,
    LoadFilters,
    LoadListItem,
    LoadSummary,
    LoadTimelineEvent,
)
from db_client import read_df
from repositories import document_repo, load_query_repo

_SELECT_COLUMNS = """
    id as _row_id,
    type as "TYPE",
    booking_number as "Booking Number",
    reference_number as "Reference Number",
    container_number as "Container Number",
    customer as "Customer",
    status as "Status",
    driver_name as "Driver Name",
    truck_assigned as "Truck Assigned",
    updated_at
"""


def _to_summary(row) -> LoadSummary:
    return LoadSummary(
        id=int(row["_row_id"]),
        type=str(row.get("TYPE") or ""),
        booking_number=str(row.get("Booking Number") or ""),
        reference_number=str(row.get("Reference Number") or ""),
        container_number=str(row.get("Container Number") or ""),
        customer=str(row.get("Customer") or ""),
        status=str(row.get("Status") or ""),
        driver_name=str(row.get("Driver Name") or ""),
        truck_assigned=str(row.get("Truck Assigned") or ""),
        updated_at=row.get("updated_at"),
    )


def list_loads(*, status: str | None = None, limit: int = 100) -> list[LoadSummary]:
    """SQL-paginated (LIMIT, no full-table Pandas load then .head()).
    `limit` is bounded server-side; the API layer applies its own Query
    bound too (see api/schemas/loads.py) - both agree on the same cap so
    neither can be bypassed by calling the application function directly."""
    bounded_limit = max(min(int(limit or 100), 100), 1)
    params: dict = {"limit": bounded_limit}
    where_sql = ""
    if status:
        where_sql = "where status = :status"
        params["status"] = status

    df = read_df(
        f"""
        select {_SELECT_COLUMNS}
        from loads
        {where_sql}
        order by updated_at desc, id desc
        limit :limit
        """,
        params,
    )
    return [_to_summary(row) for _, row in df.iterrows()]


def get_load(load_id: int) -> LoadSummary:
    df = read_df(
        f"select {_SELECT_COLUMNS} from loads where id = :id",
        {"id": int(load_id)},
    )
    if df.empty:
        raise NotFoundError(f"Load {load_id} not found.")
    return _to_summary(df.iloc[0])


# ---------------------------------------------------------------------------
# Phase 8: paginated/filtered/sorted/searched load collection + detail +
# related-resource reads. Additive - list_loads/get_load above are
# unchanged and still used by their existing callers (e.g.
# api/routers/loads.py::assign_driver_endpoint).
# ---------------------------------------------------------------------------


def _to_list_item(row) -> LoadListItem:
    return LoadListItem(
        id=int(row["id"]),
        type=str(row.get("type") or ""),
        booking_number=str(row.get("booking_number") or ""),
        reference_number=str(row.get("reference_number") or ""),
        container_number=str(row.get("container_number") or ""),
        customer=str(row.get("customer") or ""),
        port=str(row.get("port") or ""),
        warehouse=str(row.get("warehouse") or ""),
        status=str(row.get("status") or ""),
        driver_name=str(row.get("driver_name") or ""),
        truck_assigned=str(row.get("truck_assigned") or ""),
        delivery_need_date=row.get("delivery_need_date"),
        document_cutoff=row.get("document_cutoff"),
        invoice_status=str(row.get("invoice_status") or ""),
        driver_pay_status=str(row.get("driver_pay_status") or ""),
        updated_at=row.get("updated_at"),
    )


def search_loads(
    *,
    filters: LoadFilters | None = None,
    page: int = 1,
    page_size: int = load_query_repo.DEFAULT_PAGE_SIZE,
    sort_by: str = load_query_repo.DEFAULT_SORT_BY,
    sort_direction: str = load_query_repo.DEFAULT_SORT_DIRECTION,
) -> PageResult[LoadListItem]:
    """Server-side paginated, sorted, filtered load collection - never
    loads the full `loads` table into Pandas then slices/filters
    client-side (the pattern this replaces - see services/
    tms_data_service.py::load_tms_data, still used by Streamlit pages
    that haven't migrated yet)."""
    filters = filters or LoadFilters()
    resolved_page = normalize_page(page)
    resolved_page_size = normalize_page_size(page_size)
    resolved_sort_by, resolved_direction = load_query_repo.normalize_sort(sort_by, sort_direction)

    where_sql, params = load_query_repo.build_load_filters(
        status=filters.status,
        service_flow=filters.service_flow,
        customer=filters.customer,
        driver_name=filters.driver_name,
        port=filters.port,
        warehouse=filters.warehouse,
        invoice_status=filters.invoice_status,
        search=filters.search,
        delivery_after=filters.delivery_after,
        delivery_before=filters.delivery_before,
    )

    total_items = load_query_repo.count_loads(where_sql, params)
    total_pages = max(math.ceil(total_items / resolved_page_size), 1) if total_items else 1
    resolved_page = min(resolved_page, total_pages)

    df = load_query_repo.list_loads_page(
        where_sql,
        params,
        sort_by=resolved_sort_by,
        sort_direction=resolved_direction,
        page=resolved_page,
        page_size=resolved_page_size,
    )

    return PageResult(
        items=[_to_list_item(row) for _, row in df.iterrows()],
        page=resolved_page,
        page_size=resolved_page_size,
        total_items=total_items,
        total_pages=total_pages,
        sort_by=resolved_sort_by,
        sort_direction=resolved_direction,
    )


def get_load_detail(load_id: int) -> LoadDetail:
    """One consolidated read for a single load's core fields - no
    timeline, no communications, no documents (see get_load_timeline/
    get_load_communications/get_load_documents for those, each its own
    independently-paginated resource)."""
    row = load_query_repo.get_load_detail_row(load_id)
    if row is None:
        raise NotFoundError(f"Load {load_id} not found.")

    return LoadDetail(
        id=int(row["id"]),
        type=str(row.get("type") or ""),
        load_id=str(row.get("load_id") or ""),
        booking_number=str(row.get("booking_number") or ""),
        reference_number=str(row.get("reference_number") or ""),
        container_number=str(row.get("container_number") or ""),
        customer=str(row.get("customer") or ""),
        port=str(row.get("port") or ""),
        warehouse=str(row.get("warehouse") or ""),
        address=str(row.get("address") or ""),
        document_cutoff=row.get("document_cutoff"),
        delivery_need_date=row.get("delivery_need_date"),
        load_date=row.get("load_date"),
        lfd=row.get("lfd"),
        status=str(row.get("status") or ""),
        driver_name=str(row.get("driver_name") or ""),
        truck_assigned=str(row.get("truck_assigned") or ""),
        chassis=str(row.get("chassis") or ""),
        size=str(row.get("size") or ""),
        billing_notes=str(row.get("billing_notes") or ""),
        dispatcher_notes=str(row.get("dispatcher_notes") or ""),
        invoice_status=str(row.get("invoice_status") or ""),
        driver_pay_status=str(row.get("driver_pay_status") or ""),
        closeout_stage=str(row.get("closeout_stage") or ""),
        steamship_line=str(row.get("steamship_line") or ""),
        vessel_name=str(row.get("vessel_name") or ""),
        terminal=str(row.get("terminal") or ""),
        pickup_appointment=row.get("pickup_appointment"),
        delivery_appointment=row.get("delivery_appointment"),
        empty_return_location=str(row.get("empty_return_location") or ""),
        empty_return_date=row.get("empty_return_date"),
        parent_booking_key=str(row.get("parent_booking_key") or ""),
        container_sequence=int_or_none(row.get("container_sequence")),
        container_total=int_or_none(row.get("container_total")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _require_load_exists(load_id: int) -> None:
    """Related-resource endpoints (timeline/communications/documents)
    must 404 for a load that doesn't exist, not silently return an empty
    page - a caller can't otherwise distinguish "load exists, no
    events yet" from "load id is wrong"."""
    if load_query_repo.get_load_detail_row(load_id) is None:
        raise NotFoundError(f"Load {load_id} not found.")


def get_load_timeline(load_id: int, *, page: int = 1, page_size: int = 50) -> PageResult[LoadTimelineEvent]:
    _require_load_exists(load_id)
    resolved_page = normalize_page(page)
    resolved_page_size = normalize_page_size(page_size)

    total_items = load_query_repo.count_load_timeline_events(load_id)
    total_pages = max(math.ceil(total_items / resolved_page_size), 1) if total_items else 1
    resolved_page = min(resolved_page, total_pages)

    df = load_query_repo.list_load_timeline_page(load_id, page=resolved_page, page_size=resolved_page_size)

    items = [
        LoadTimelineEvent(
            event_type=str(row.get("event_type") or ""),
            title=str(row.get("title") or ""),
            details=str(row.get("details") or ""),
            actor=str(row.get("actor") or ""),
            created_at=row.get("created_at"),
        )
        for _, row in df.iterrows()
    ]
    return PageResult(items=items, page=resolved_page, page_size=resolved_page_size, total_items=total_items, total_pages=total_pages)


def get_load_communications(load_id: int, *, page: int = 1, page_size: int = 50) -> PageResult[LoadCommunication]:
    _require_load_exists(load_id)
    resolved_page = normalize_page(page)
    resolved_page_size = normalize_page_size(page_size)

    total_items = load_query_repo.count_load_communications(load_id)
    total_pages = max(math.ceil(total_items / resolved_page_size), 1) if total_items else 1
    resolved_page = min(resolved_page, total_pages)

    df = load_query_repo.list_load_communications_page(load_id, page=resolved_page, page_size=resolved_page_size)

    items = [
        LoadCommunication(
            id=int(row["id"]),
            message_type=str(row.get("message_type") or ""),
            direction=str(row.get("direction") or ""),
            recipient=str(row.get("recipient") or ""),
            message_body=str(row.get("message_body") or ""),
            sent_by=str(row.get("sent_by") or ""),
            provider=str(row.get("provider") or ""),
            delivery_status=str(row.get("delivery_status") or ""),
            provider_message_id=str(row.get("provider_message_id") or ""),
            created_at=row.get("created_at"),
        )
        for _, row in df.iterrows()
    ]
    return PageResult(items=items, page=resolved_page, page_size=resolved_page_size, total_items=total_items, total_pages=total_pages)


def get_load_documents(load_id: int, *, limit: int = 50) -> list[LoadDocumentMeta]:
    """Not paginated (STEP 12 doesn't ask for it, and per-load document
    counts are small - a bounded list is sufficient); see
    application.attachments/application.work_items for the equivalent
    Operations Inbox pattern, which also doesn't paginate attachments."""
    _require_load_exists(load_id)
    rows = document_repo.list_documents_for_load(load_id, limit=limit)
    return [
        LoadDocumentMeta(
            id=int(row["id"]),
            document_type=str(row.get("document_type") or ""),
            filename=str(row.get("filename") or ""),
            source=str(row.get("source") or ""),
            status=str(row.get("status") or ""),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]


def int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
