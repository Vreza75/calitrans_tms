from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db_client import DispatchDatabaseClient, read_df

from api.errors import register_error_handlers
from api.routers import attachments, health, loads, work_items

app = FastAPI(title="Calitrans TMS Integration API")

register_error_handlers(app)

# Phase 1 has no frontend consumer yet (Next.js is out of scope for this
# phase - see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md). Restrict
# CORS to an explicit allowlist read from CORS_ALLOWED_ORIGINS (comma-
# separated) instead of "*", so a future frontend origin is added
# deliberately rather than the API defaulting open.
_allowed_origins = [origin.strip() for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

app.include_router(health.router, prefix="/api/v1")
app.include_router(work_items.router, prefix="/api/v1")
app.include_router(loads.router, prefix="/api/v1")
app.include_router(attachments.router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Legacy endpoints (pre-Phase 1). Kept for backward compatibility with any
# existing caller of the unversioned API - do not add new functionality
# here. New work goes through /api/v1 (see api/routers/).
# ---------------------------------------------------------------------------


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


@app.get("/health", tags=["legacy"])
def health_legacy() -> dict[str, str]:
    """Legacy unversioned health check. Prefer GET /api/v1/health."""
    return {"status": "ok"}


@app.get("/loads", tags=["legacy"])
def list_loads_legacy(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Legacy unversioned loads list. Prefer GET /api/v1/loads."""
    sql = "select * from loads"
    params: dict[str, Any] = {"limit": limit}

    if status:
        sql += " where status = :status"
        params["status"] = status

    sql += " order by updated_at desc limit :limit"

    return read_df(sql, params).to_dict(orient="records")


@app.post("/loads", tags=["legacy"])
def create_load_legacy(payload: LoadCreateRequest) -> dict[str, Any]:
    """Legacy unversioned load creation. Prefer POST /api/v1/work-items/{id}/create-load."""
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
