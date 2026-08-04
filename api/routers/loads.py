from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import MUTATE_OPERATIONS, READ_LOADS, require_role
from api.schemas.loads import AssignDriverIn, LoadSummaryOut, TransitionLoadIn, TransitionResultOut
from application.loads.commands import transition_load
from application.loads.queries import get_load, list_loads

router = APIRouter(prefix="/loads", tags=["loads"])


@router.get("", response_model=list[LoadSummaryOut], dependencies=[Depends(require_role(*READ_LOADS))])
def list_loads_endpoint(status: str | None = None, limit: int = 100) -> list[LoadSummaryOut]:
    loads = list_loads(status=status, limit=limit)
    return [LoadSummaryOut.from_domain(load) for load in loads]


@router.get("/{load_id}", response_model=LoadSummaryOut, dependencies=[Depends(require_role(*READ_LOADS))])
def get_load_endpoint(load_id: int) -> LoadSummaryOut:
    return LoadSummaryOut.from_domain(get_load(load_id))


@router.post(
    "/{load_id}/transition",
    response_model=TransitionResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def transition_load_endpoint(load_id: int, payload: TransitionLoadIn) -> TransitionResultOut:
    result = transition_load(
        load_id,
        payload.new_status,
        note=payload.note,
        driver=payload.driver,
        truck=payload.truck,
        override=payload.override,
        override_reason=payload.override_reason,
    )
    return TransitionResultOut(**result.__dict__)


@router.post(
    "/{load_id}/assign-driver",
    response_model=TransitionResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def assign_driver_endpoint(load_id: int, payload: AssignDriverIn) -> TransitionResultOut:
    """Assignment without a status change: re-applies the load's current
    status through the same transactional apply_transition() path, so the
    driver/truck write and its audit row are still atomic."""
    from application.loads.queries import get_load as _get_load

    current = _get_load(load_id)
    result = transition_load(load_id, current.status, driver=payload.driver, truck=payload.truck)
    return TransitionResultOut(**result.__dict__)
