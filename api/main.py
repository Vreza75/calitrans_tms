from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db_client import DispatchDatabaseClient, read_df


app = FastAPI(title="Calitrans TMS Integration API")


class LoadCreateRequest(BaseModel):
    type: str | None = "OTR Export"
    booking_number: str
    reference_number: str | None = None
    container_number: str | None = None
    customer: str | None = None
    port: str | None = None
    warehouse: str | None = None
    delivery_need_date: str | None = None
    document_cutoff: str | None = None
    dispatcher_notes: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/loads")
def list_loads(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "select * from loads"
    params: dict[str, Any] = {"limit": limit}

    if status:
        sql += " where status = :status"
        params["status"] = status

    sql += " order by updated_at desc limit :limit"

    return read_df(sql, params).to_dict(orient="records")


@app.post("/loads")
def create_load(payload: LoadCreateRequest) -> dict[str, Any]:
    client = DispatchDatabaseClient()

    row_data = {
        "TYPE": payload.type,
        "Booking Number": payload.booking_number,
        "Reference Number": payload.reference_number,
        "Container Number": payload.container_number,
        "Customer": payload.customer,
        "Port": payload.port,
        "Warehouse": payload.warehouse,
        "Delivery Need Date": payload.delivery_need_date,
        "Document Cutoff": payload.document_cutoff,
        "Status": "New",
        "Dispatcher Notes": payload.dispatcher_notes,
    }

    try:
        created = client.add_row(row_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": created.id, "booking_number": payload.booking_number}
