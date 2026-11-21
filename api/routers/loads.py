from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile

from api.auth import (
    DOCUMENT_MUTATE,
    MUTATE_OPERATIONS,
    READ_LOADS,
    AuthenticatedActor,
    require_auth,
    require_role,
)
from api.schemas.loads import (
    AssignDriverIn,
    DocumentUploadOut,
    LoadCommandResultOut,
    LoadCommunicationOut,
    LoadCommunicationPageOut,
    LoadDetailOut,
    LoadDocumentMetaOut,
    LoadListItemOut,
    LoadNoteOnlyIn,
    LoadPageOut,
    LoadSummaryOut,
    LoadTimelineEventOut,
    LoadTimelinePageOut,
    LogCommunicationIn,
    LogCommunicationOut,
    OperationalStagesOut,
    SaveLoadNoteIn,
    TransitionLoadIn,
    TransitionResultOut,
    UpdateLoadFieldsIn,
)
from application.dispatch.commands import log_dispatch_communication
from application.documents.commands import attach_load_document
from application.loads.commands import (
    cancel_load,
    mark_load_missing_info,
    save_load_note,
    transition_load,
    update_load_fields,
    verify_load_booking,
)
from application.loads.models import LoadFilters
from application.loads.queries import (
    get_load,
    get_load_communications,
    get_load_detail,
    get_load_documents,
    get_load_timeline,
    get_operational_stages,
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


@router.get("/stages", response_model=OperationalStagesOut, dependencies=[Depends(require_role(*READ_LOADS))])
def get_operational_stages_endpoint(service_flow: str | None = None) -> OperationalStagesOut:
    """Phase 10B: the Dispatch Board's column set for a given
    service_flow - must be registered before GET /{load_id} so "/stages"
    is never matched as a load_id path parameter."""
    return OperationalStagesOut(stages=get_operational_stages(service_flow))


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
    driver/truck write and its audit row are still atomic.

    new_status=None, not a status read here first - a status read by this
    router before apply_transition's row lock is taken could already be
    stale by the time the lock lands, and handing that stale value back
    as the transition target would make apply_transition validate it as
    a (spurious) backward move instead of the assignment-only request it
    actually is. See application/loads/commands.py::transition_load and
    services/dispatch_transition_service.py::apply_transition."""
    result = transition_load(
        load_id,
        None,
        actor=actor,
        driver=payload.driver,
        truck=payload.truck,
        expected_driver=payload.expected_driver,
        expected_truck=payload.expected_truck,
    )
    return TransitionResultOut(**result.__dict__)


# ---------------------------------------------------------------------------
# Phase 10C: Load Workspace. Every endpoint below is a thin wrapper over an
# existing, already authorization-gated application command - no business
# logic, no direct database access, lives here. See docs/architecture/
# PHASE_10C_LOAD_WORKSPACE_PLAN.md.
# ---------------------------------------------------------------------------


@router.put(
    "/{load_id}",
    response_model=LoadCommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def update_load_fields_endpoint(
    load_id: int, payload: UpdateLoadFieldsIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LoadCommandResultOut:
    """Free-form multi-field save - the Next.js equivalent of the
    Streamlit Order Detail Editor's "Save Order Updates" button. Wraps
    application.loads.commands.update_load_fields unchanged; that
    function is the existing allowlist/validation boundary for which
    columns may be written."""
    result = update_load_fields(actor=actor, load_id=load_id, updates=payload.updates)
    return LoadCommandResultOut(**result.__dict__)


@router.post(
    "/{load_id}/notes",
    response_model=LoadCommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def save_load_note_endpoint(
    load_id: int, payload: SaveLoadNoteIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LoadCommandResultOut:
    """Notes-only save, distinct from update_load_fields_endpoint above -
    mirrors Streamlit's separate "Save Notes" button not requiring the
    rest of the form to be resubmitted."""
    result = save_load_note(actor=actor, load_id=load_id, note=payload.note)
    return LoadCommandResultOut(**result.__dict__)


@router.post(
    "/{load_id}/mark-missing-info",
    response_model=LoadCommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def mark_load_missing_info_endpoint(
    load_id: int, payload: LoadNoteOnlyIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LoadCommandResultOut:
    result = mark_load_missing_info(actor=actor, load_id=load_id, note=payload.note)
    return LoadCommandResultOut(**result.__dict__)


@router.post(
    "/{load_id}/verify-booking",
    response_model=LoadCommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def verify_load_booking_endpoint(
    load_id: int, payload: LoadNoteOnlyIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LoadCommandResultOut:
    result = verify_load_booking(actor=actor, load_id=load_id, note=payload.note)
    return LoadCommandResultOut(**result.__dict__)


@router.post(
    "/{load_id}/cancel",
    response_model=LoadCommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def cancel_load_endpoint(
    load_id: int, payload: LoadNoteOnlyIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LoadCommandResultOut:
    result = cancel_load(actor=actor, load_id=load_id, note=payload.note)
    return LoadCommandResultOut(**result.__dict__)


@router.post(
    "/{load_id}/communications",
    response_model=LogCommunicationOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def log_load_communication_endpoint(
    load_id: int, payload: LogCommunicationIn, actor: AuthenticatedActor = Depends(require_auth)
) -> LogCommunicationOut:
    """Manual communication-log entry (Streamlit's "Save Message /
    Driver Communication / Customer Note / Operational Note" buttons) -
    `provider` is fixed to "internal" here, not accepted from the
    client: this endpoint logs a note, it does not send anything."""
    log_dispatch_communication(
        actor=actor,
        load_id=load_id,
        message_type=payload.message_type,
        direction=payload.direction,
        recipient=payload.recipient,
        message_body=payload.message_body,
        provider="internal",
    )
    return LogCommunicationOut(ok=True)


class _SyncUploadAdapter:
    """Adapts FastAPI's UploadFile to the sync .name/.seek()/.read()
    interface services.document_storage_service.stage_upload() expects
    (built for Streamlit's UploadedFile) - UploadFile.file is the
    underlying SpooledTemporaryFile, which already has sync seek()/
    read(); only .filename -> .name needs renaming. No change to
    stage_upload or attach_load_document themselves."""

    def __init__(self, upload: UploadFile) -> None:
        self.name = upload.filename or "upload"
        self._file = upload.file

    def seek(self, offset: int) -> int:
        return self._file.seek(offset)

    def read(self) -> bytes:
        return self._file.read()


@router.post(
    "/{load_id}/documents",
    response_model=DocumentUploadOut,
    dependencies=[Depends(require_role(*DOCUMENT_MUTATE))],
)
def attach_load_document_endpoint(
    load_id: int, file: UploadFile = File(...), actor: AuthenticatedActor = Depends(require_auth)
) -> DocumentUploadOut:
    """Accounting is included in this route's role gate (DOCUMENT_MUTATE,
    not MUTATE_OPERATIONS) because Permission.DOCUMENT_ATTACH grants
    Accounting this specific capability - see api/auth.py's
    DOCUMENT_MUTATE definition."""
    document_id = attach_load_document(actor=actor, load_id=load_id, uploaded_file=_SyncUploadAdapter(file), source="web")
    return DocumentUploadOut(ok=True, document_id=document_id, status="pending")
