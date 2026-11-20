from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import READ_LOADS, require_role
from api.schemas.drivers import DriverOut
from application.drivers.queries import list_active_drivers

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/active", response_model=list[DriverOut], dependencies=[Depends(require_role(*READ_LOADS))])
def list_active_drivers_endpoint() -> list[DriverOut]:
    """Active roster drivers for the Dispatch Board's assignment dropdown
    (Phase 10B). Same readers as GET /loads/search (READ_LOADS) - seeing
    which drivers are available is part of load visibility, not a
    separate capability."""
    return [DriverOut.from_domain(driver) for driver in list_active_drivers()]
