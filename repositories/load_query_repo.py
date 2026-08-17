# repositories/load_query_repo.py

"""Phase 8: server-side paginated/filtered/sorted/searched reads over
`loads`. Framework-neutral: no Streamlit import, no rendering, no
session state.

Distinct from `repositories/load_repo.py` (if/when it holds mutation-
adjacent reads) and from `application/loads/queries.py::list_loads`
(Phase 5/6's original, still used as-is by callers that only need a
status filter with no pagination/search/sort - not touched here). This
module is the query-repository layer STEP 4/17 asks for: explicit
columns (never `select *`), parameterized SQL, an allowlisted sort
mapping (never raw user input in `ORDER BY`), same shape as
`repositories/work_item_repo.py`'s proven Operations Queue pattern.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from db_client import read_df

# Public sort key -> real SQL expression. Whitelisted so sort_by can
# never be interpolated into SQL directly (no injection surface) - same
# convention as repositories/work_item_repo.py::SORTABLE_COLUMNS.
SORTABLE_COLUMNS: dict[str, str] = {
    "updated_at": "updated_at",
    "created_at": "created_at",
    "delivery_need_date": "delivery_need_date",
    "document_cutoff": "document_cutoff",
    "status": "status",
    "booking_number": "booking_number",
}

DEFAULT_SORT_BY = "updated_at"
DEFAULT_SORT_DIRECTION = "desc"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_LIST_COLUMNS = """
    id,
    type,
    booking_number,
    reference_number,
    container_number,
    customer,
    port,
    warehouse,
    status,
    driver_name,
    truck_assigned,
    delivery_need_date,
    document_cutoff,
    invoice_status,
    driver_pay_status,
    updated_at
"""

_DETAIL_COLUMNS = """
    id,
    type,
    load_id,
    booking_number,
    reference_number,
    container_number,
    customer,
    port,
    warehouse,
    address,
    document_cutoff,
    delivery_need_date,
    load_date,
    lfd,
    status,
    driver_name,
    truck_assigned,
    chassis,
    size,
    billing_notes,
    dispatcher_notes,
    invoice_status,
    driver_pay_status,
    closeout_stage,
    steamship_line,
    vessel_name,
    terminal,
    pickup_appointment,
    delivery_appointment,
    empty_return_location,
    empty_return_date,
    parent_booking_key,
    container_sequence,
    container_total,
    created_at,
    updated_at
"""


def normalize_sort(sort_by: str | None, sort_direction: str | None) -> tuple[str, str]:
    resolved_sort_by = sort_by if sort_by in SORTABLE_COLUMNS else DEFAULT_SORT_BY
    resolved_direction = str(sort_direction or DEFAULT_SORT_DIRECTION).strip().lower()
    if resolved_direction not in {"asc", "desc"}:
        resolved_direction = DEFAULT_SORT_DIRECTION
    return resolved_sort_by, resolved_direction


def normalize_page_size(page_size: int | None) -> int:
    if not page_size:
        return DEFAULT_PAGE_SIZE
    return max(min(int(page_size), MAX_PAGE_SIZE), 1)


def build_load_filters(
    *,
    status: str | None = None,
    service_flow: str | None = None,
    customer: str | None = None,
    driver_name: str | None = None,
    port: str | None = None,
    warehouse: str | None = None,
    invoice_status: str | None = None,
    search: str | None = None,
    delivery_after: str | None = None,
    delivery_before: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a WHERE clause (no leading "where") and bound params. Every
    value is bound - none are string-formatted into the SQL - safe
    against injection regardless of what a caller (Streamlit form,
    FastAPI query param) passes in.

    Only filters on columns that actually exist on `loads` today (see
    database/schema.sql, database/portpro_style_migration.sql,
    database/multi_container_migration.sql) - no `dispatcher` filter,
    because no such column exists on this table (STEP 5: "Only implement
    fields actually present and meaningful in the current schema. Do not
    invent fields.")."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if status:
        clauses.append("status = :status")
        params["status"] = status

    if service_flow:
        clauses.append("type = :service_flow")
        params["service_flow"] = service_flow

    if customer:
        clauses.append("customer ilike :customer")
        params["customer"] = f"%{customer}%"

    if driver_name:
        clauses.append("driver_name ilike :driver_name")
        params["driver_name"] = f"%{driver_name}%"

    if port:
        clauses.append("port ilike :port")
        params["port"] = f"%{port}%"

    if warehouse:
        clauses.append("warehouse ilike :warehouse")
        params["warehouse"] = f"%{warehouse}%"

    if invoice_status:
        clauses.append("invoice_status = :invoice_status")
        params["invoice_status"] = invoice_status

    if delivery_after:
        clauses.append("delivery_need_date >= :delivery_after")
        params["delivery_after"] = delivery_after

    if delivery_before:
        clauses.append("delivery_need_date <= :delivery_before")
        params["delivery_before"] = delivery_before

    if search:
        clauses.append(
            """(
                booking_number ilike :search
                or container_number ilike :search
                or reference_number ilike :search
                or customer ilike :search
                or driver_name ilike :search
                or truck_assigned ilike :search
            )"""
        )
        params["search"] = f"%{search}%"

    where_sql = " and ".join(clauses) if clauses else "true"
    return where_sql, params


def count_loads(where_sql: str, params: dict[str, Any]) -> int:
    df = read_df(f"select count(*) as total from loads where {where_sql}", params)
    if df.empty:
        return 0
    return int(df.iloc[0]["total"])


def list_loads_page(
    where_sql: str,
    params: dict[str, Any],
    *,
    sort_by: str,
    sort_direction: str,
    page: int,
    page_size: int,
) -> pd.DataFrame:
    """One page of the loads collection, filtered/sorted/paginated in
    PostgreSQL - never the full table pulled into Pandas and sliced
    client-side (the pattern this repository replaces, per
    services/tms_data_service.py::load_tms_data's unbounded reads)."""
    sort_column = SORTABLE_COLUMNS.get(sort_by, SORTABLE_COLUMNS[DEFAULT_SORT_BY])
    direction = "asc" if sort_direction == "asc" else "desc"
    offset = max(page - 1, 0) * page_size

    query_params = dict(params)
    query_params["limit"] = page_size
    query_params["offset"] = offset

    return read_df(
        f"""
        select {_LIST_COLUMNS}
        from loads
        where {where_sql}
        order by {sort_column} {direction}, id desc
        limit :limit offset :offset
        """,
        query_params,
    )


def get_load_detail_row(load_id: int) -> dict | None:
    df = read_df(f"select {_DETAIL_COLUMNS} from loads where id = :id", {"id": int(load_id)})
    if df.empty:
        return None
    return df.iloc[0].to_dict()


# ---------------------------------------------------------------------------
# Load timeline (STEP 10). Unifies status_events + dispatch_messages, both
# already load_id-scoped and chronologically ordered - not a fabricated
# unified event model (STEP 10: "Do not fabricate a unified event model
# if existing schemas do not support it cleanly"), just two existing,
# directly-joinable-by-load_id tables merged and paginated.
# operations_case_events is deliberately NOT included - it's keyed by
# case_id, not load_id, and joining through order_intake/case linkage to
# reach it is a bigger unification than "at minimum provide chronological
# operational history" calls for.
# ---------------------------------------------------------------------------

_TIMELINE_UNION_SQL = """
    (
        select
            'status_change' as event_type,
            new_status as title,
            notes as details,
            created_by as actor,
            created_at
        from status_events
        where load_id = :load_id
    )
    union all
    (
        select
            'dispatch_message' as event_type,
            message_type as title,
            message_body as details,
            sent_by as actor,
            created_at
        from dispatch_messages
        where load_id = :load_id
    )
"""


def count_load_timeline_events(load_id: int) -> int:
    df = read_df(f"select count(*) as total from ({_TIMELINE_UNION_SQL}) as timeline", {"load_id": int(load_id)})
    if df.empty:
        return 0
    return int(df.iloc[0]["total"])


def list_load_timeline_page(load_id: int, *, page: int, page_size: int) -> pd.DataFrame:
    offset = max(page - 1, 0) * page_size
    return read_df(
        f"""
        select * from ({_TIMELINE_UNION_SQL}) as timeline
        order by created_at desc
        limit :limit offset :offset
        """,
        {"load_id": int(load_id), "limit": page_size, "offset": offset},
    )


# ---------------------------------------------------------------------------
# Load communications (STEP 11) - dispatch_messages only, already
# load_id-scoped. Foundational for a later Communications Hub; does not
# implement inbound SMS (out of scope, per STEP 11).
# ---------------------------------------------------------------------------

_COMMUNICATIONS_COLUMNS = """
    id,
    message_type,
    direction,
    recipient,
    message_body,
    sent_by,
    provider,
    delivery_status,
    provider_message_id,
    created_at
"""


def count_load_communications(load_id: int) -> int:
    df = read_df("select count(*) as total from dispatch_messages where load_id = :load_id", {"load_id": int(load_id)})
    if df.empty:
        return 0
    return int(df.iloc[0]["total"])


def list_load_communications_page(load_id: int, *, page: int, page_size: int) -> pd.DataFrame:
    offset = max(page - 1, 0) * page_size
    return read_df(
        f"""
        select {_COMMUNICATIONS_COLUMNS}
        from dispatch_messages
        where load_id = :load_id
        order by created_at desc
        limit :limit offset :offset
        """,
        {"load_id": int(load_id), "limit": page_size, "offset": offset},
    )
