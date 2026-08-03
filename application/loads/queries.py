from __future__ import annotations

from application.exceptions import NotFoundError
from application.loads.models import LoadSummary
from db_client import DispatchDatabaseClient


def list_loads(*, status: str | None = None, limit: int = 100) -> list[LoadSummary]:
    df = DispatchDatabaseClient().rows_to_dataframe()
    if status:
        df = df[df["Status"] == status]
    df = df.head(max(int(limit or 100), 1))

    return [
        LoadSummary(
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
        for _, row in df.iterrows()
    ]


def get_load(load_id: int) -> LoadSummary:
    df = DispatchDatabaseClient().rows_to_dataframe()
    match = df[df["_row_id"] == int(load_id)]
    if match.empty:
        raise NotFoundError(f"Load {load_id} not found.")

    row = match.iloc[0]
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
