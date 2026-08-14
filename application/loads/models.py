from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    reason: str
    status: str
    closeout_stage: str


@dataclass(frozen=True)
class CreateLoadResult:
    ok: bool
    load_id: int | None
    review_status: str = ""
    reason: str = ""


@dataclass(frozen=True)
class UpdateLoadResult:
    ok: bool
    load_id: int | None
    updated_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class LoadCommandResult:
    """Shared result shape for the single/few-field load mutations added
    in Phase 5 (mark_load_missing_info, save_load_note,
    verify_load_booking, cancel_load, update_load_fields) - each is a
    permission-gated wrapper around one DispatchDatabaseClient.
    update_row_fields() call, so they share one result shape rather than
    each inventing a near-identical dataclass."""

    ok: bool
    load_id: int
    status: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ReadyToDispatchResult:
    """Phase 6: `sms_status` replaced the old `sms_sent: bool` - the SMS
    is now enqueued to the transactional outbox (services/
    outbox_processor.py delivers it asynchronously), so this command can
    no longer truthfully report "sent" by the time it returns. Values:
    "queued" on success, "" when ok is False (invalid phone/unauthorized -
    nothing was enqueued)."""

    ok: bool
    load_id: int
    status: str = ""
    sms_status: str = ""
    reason: str = ""


@dataclass(frozen=True)
class LoadSummary:
    id: int
    type: str
    booking_number: str
    reference_number: str
    container_number: str
    customer: str
    status: str
    driver_name: str
    truck_assigned: str
    updated_at: Any


# ---------------------------------------------------------------------------
# Phase 8: read models for the paginated/filtered/searchable load
# collection (application/loads/queries.py::search_loads,
# get_load_detail, get_load_timeline, get_load_communications,
# get_load_documents) - additive, does not replace LoadSummary/list_loads/
# get_load above, which Phase 5/6 callers still use unchanged.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadFilters:
    status: str | None = None
    service_flow: str | None = None
    customer: str | None = None
    driver_name: str | None = None
    port: str | None = None
    warehouse: str | None = None
    invoice_status: str | None = None
    search: str | None = None
    delivery_after: str | None = None
    delivery_before: str | None = None


@dataclass(frozen=True)
class LoadListItem:
    """List DTO - deliberately lighter than LoadDetail (STEP: "list DTO
    != detail DTO"). `updated_at` doubles as this record's staleness/
    change indicator (STEP 25) - no version column added solely for
    that, per STEP 25's own guidance not to."""

    id: int
    type: str
    booking_number: str
    reference_number: str
    container_number: str
    customer: str
    port: str
    warehouse: str
    status: str
    driver_name: str
    truck_assigned: str
    delivery_need_date: Any
    document_cutoff: Any
    invoice_status: str
    driver_pay_status: str
    updated_at: Any


@dataclass(frozen=True)
class LoadDetail:
    """Richer than LoadListItem, but deliberately still excludes large
    related collections (timeline, communications, documents) - those
    are separate, independently-paginated resources
    (get_load_timeline/get_load_communications/get_load_documents), not
    eagerly joined in here."""

    id: int
    type: str
    load_id: str
    booking_number: str
    reference_number: str
    container_number: str
    customer: str
    port: str
    warehouse: str
    address: str
    document_cutoff: Any
    delivery_need_date: Any
    load_date: Any
    lfd: Any
    status: str
    driver_name: str
    truck_assigned: str
    chassis: str
    size: str
    billing_notes: str
    dispatcher_notes: str
    invoice_status: str
    driver_pay_status: str
    closeout_stage: str
    steamship_line: str
    vessel_name: str
    terminal: str
    pickup_appointment: Any
    delivery_appointment: Any
    empty_return_location: str
    empty_return_date: Any
    parent_booking_key: str
    container_sequence: int | None
    container_total: int | None
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class LoadTimelineEvent:
    event_type: str
    title: str
    details: str
    actor: str
    created_at: Any


@dataclass(frozen=True)
class LoadCommunication:
    id: int
    message_type: str
    direction: str
    recipient: str
    message_body: str
    sent_by: str
    provider: str
    delivery_status: str
    provider_message_id: str
    created_at: Any


@dataclass(frozen=True)
class LoadDocumentMeta:
    id: int
    document_type: str
    filename: str
    source: str
    status: str
    created_at: Any
