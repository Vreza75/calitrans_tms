from __future__ import annotations

from application.exceptions import NotFoundError
from application.loads.models import LoadSummary
from db_client import read_df

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
