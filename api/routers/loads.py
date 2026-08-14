from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import MUTATE_OPERATIONS, READ_LOADS, AuthenticatedActor, require_auth, require_role
from api.schemas.loads import (
    AssignDriverIn,
    LoadCommunicationOut,
    LoadCommunicationPageOut,
    LoadDetailOut,
    LoadDocumentMetaOut,
    LoadListItemOut,
    LoadPageOut,
    LoadSummaryOut,
    LoadTimelineEventOut,
    LoadTimelinePageOut,
    TransitionLoadIn,
    TransitionResultOut,
)
from application.loads.commands import transition_load
from application.loads.models import LoadFilters
from application.loads.queries import (
    get_load,
    get_load_communications,
    get_load_detail,
    get_load_documents,
    get_load_timeline,
    list_loads,
    search_loads,
)

router = APIRouter(prefix="/loads", tags=["loads"])


@router.get("", response_model=list[LoadSummaryOut], dependencies=[Depends(require_role(*READ_LOADS))])
def list_loads_endpoint(status: str | None = None, limit: int = 100) -> list[LoadSummaryOut]:
    """Legacy, unpaginated - kept for any existing caller relying on a
    bare list response. Prefer GET /loads/search for anything new (STEP 8:
    "avoid hidden breaking changes" - this endpoint's shape is unchanged;
    /search is additive, not a replacement of this route)."""
    loads = list_loads(status=status, limit=limit)
    return [LoadSummaryOut.from_domain(load) for load in loads]


@router.get("/search", response_model=LoadPageOut, dependencies=[Depends(require_role(*READ_LOADS))])
def search_loads_endpoint(
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
    sort: str = "updated_at",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> LoadPageOut:
    """Server-side paginated, filtered, sorted, searched load collection -
    replaces loading the full `loads` table into a DataFrame and
    filtering/sorting/paginating it in Python (services/
    tms_data_service.py::load_tms_data's pattern)."""
    result = search_loads(
        filters=LoadFilters(
            status=status,
            service_flow=service_flow,
            customer=customer,
            driver_name=driver_name,
            port=port,
            warehouse=warehouse,
            invoice_status=invoice_status,
            search=search,
            delivery_after=delivery_after,
            delivery_before=delivery_before,
        ),
        page=page,
        page_size=limit,
        sort_by=sort,
        sort_direction=direction,
    )
    return LoadPageOut(
        items=[LoadListItemOut.from_domain(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
        sort_by=result.sort_by,
        sort_direction=result.sort_direction,
    )


@router.get("/{load_id}", response_model=LoadSummaryOut, dependencies=[Depends(require_role(*READ_LOADS))])
def get_load_endpoint(load_id: int) -> LoadSummaryOut:
    return LoadSummaryOut.from_domain(get_load(load_id))


@router.get("/{load_id}/detail", response_model=LoadDetailOut, dependencies=[Depends(require_role(*READ_LOADS))])
def get_load_detail_endpoint(load_id: int) -> LoadDetailOut:
    """Richer than GET /loads/{load_id} - excludes timeline/communications/
    documents on purpose (STEP 9: "list DTO != detail DTO", and detail
    itself should not eagerly join large related collections either).
    Not replacing GET /loads/{load_id} (that route's shape is an existing
    contract - LoadSummary - left unchanged)."""
    return LoadDetailOut.from_domain(get_load_detail(load_id))


@router.get(
    "/{load_id}/timeline", response_model=LoadTimelinePageOut, dependencies=[Depends(require_role(*READ_LOADS))]
)
def get_load_timeline_endpoint(load_id: int, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)) -> LoadTimelinePageOut:
    result = get_load_timeline(load_id, page=page, page_size=limit)
    return LoadTimelinePageOut(
        items=[LoadTimelineEventOut.from_domain(e) for e in result.items],
        page=result.page,
        page_size=result.page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
    )


@router.get(
    "/{load_id}/communications",
    response_model=LoadCommunicationPageOut,
    dependencies=[Depends(require_role(*READ_LOADS))],
)
def get_load_communications_endpoint(
    load_id: int, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)
) -> LoadCommunicationPageOut:
    result = get_load_communications(load_id, page=page, page_size=limit)
    return LoadCommunicationPageOut(
        items=[LoadCommunicationOut.from_domain(c) for c in result.items],
        page=result.page,
        page_size=result.page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
    )


@router.get(
    "/{load_id}/documents", response_model=list[LoadDocumentMetaOut], dependencies=[Depends(require_role(*READ_LOADS))]
)
def get_load_documents_endpoint(load_id: int, limit: int = Query(50, ge=1, le=200)) -> list[LoadDocumentMetaOut]:
    return [LoadDocumentMetaOut.from_domain(doc) for doc in get_load_documents(load_id, limit=limit)]


@router.post(
    "/{load_id}/transition",
    response_model=TransitionResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def transition_load_endpoint(
    load_id: int, payload: TransitionLoadIn, actor: AuthenticatedActor = Depends(require_auth)
) -> TransitionResultOut:
    result = transition_load(
        load_id,
        payload.new_status,
        actor=actor,
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
def assign_driver_endpoint(
    load_id: int, payload: AssignDriverIn, actor: AuthenticatedActor = Depends(require_auth)
) -> TransitionResultOut:
    """Assignment without a status change: re-applies the load's current
    status through the same transactional apply_transition() path, so the
    driver/truck write and its audit row are still atomic."""
    from application.loads.queries import get_load as _get_load

    current = _get_load(load_id)
    result = transition_load(load_id, current.status, actor=actor, driver=payload.driver, truck=payload.truck)
    return TransitionResultOut(**result.__dict__)
