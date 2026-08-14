from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from api.schemas.pagination import PageOut
from application.loads.models import (
    LoadCommunication,
    LoadDetail,
    LoadDocumentMeta,
    LoadListItem,
    LoadSummary,
    LoadTimelineEvent,
)


class LoadSummaryOut(BaseModel):
    id: int
    type: str
    booking_number: str
    reference_number: str
    container_number: str
    customer: str
    status: str
    driver_name: str
    truck_assigned: str
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, load: LoadSummary) -> "LoadSummaryOut":
        return cls(**load.__dict__)


class TransitionLoadIn(BaseModel):
    new_status: str
    note: str = ""
    driver: str | None = None
    truck: str | None = None
    override: bool = False
    override_reason: str = ""


class TransitionResultOut(BaseModel):
    ok: bool
    reason: str
    status: str
    closeout_stage: str


class AssignDriverIn(BaseModel):
    driver: str
    truck: str | None = None


class CreateLoadIn(BaseModel):
    approved_fields: dict[str, Any]


class CreateLoadOut(BaseModel):
    ok: bool
    load_id: int | None
    review_status: str = ""


class UpdateLoadIn(BaseModel):
    load_id: int
    approved_fields: dict[str, Any]
    fill_blank_only: bool = True


class UpdateLoadOut(BaseModel):
    ok: bool
    load_id: int | None
    updated_fields: list[str] = []
    skipped_fields: list[str] = []


# ---------------------------------------------------------------------------
# Phase 8: paginated/filtered/sorted/searched load collection + detail +
# related-resource schemas.
# ---------------------------------------------------------------------------


class LoadListItemOut(BaseModel):
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
    delivery_need_date: date | None
    document_cutoff: date | None
    invoice_status: str
    driver_pay_status: str
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, item: LoadListItem) -> "LoadListItemOut":
        return cls(**item.__dict__)


class LoadPageOut(PageOut[LoadListItemOut]):
    pass


class LoadDetailOut(BaseModel):
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
    document_cutoff: date | None
    delivery_need_date: date | None
    load_date: date | None
    lfd: date | None
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
    pickup_appointment: datetime | None
    delivery_appointment: datetime | None
    empty_return_location: str
    empty_return_date: date | None
    parent_booking_key: str
    container_sequence: int | None
    container_total: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, detail: LoadDetail) -> "LoadDetailOut":
        return cls(**detail.__dict__)


class LoadTimelineEventOut(BaseModel):
    event_type: str
    title: str
    details: str
    actor: str
    created_at: datetime | None

    @classmethod
    def from_domain(cls, event: LoadTimelineEvent) -> "LoadTimelineEventOut":
        return cls(**event.__dict__)


class LoadTimelinePageOut(PageOut[LoadTimelineEventOut]):
    pass


class LoadCommunicationOut(BaseModel):
    id: int
    message_type: str
    direction: str
    recipient: str
    message_body: str
    sent_by: str
    provider: str
    delivery_status: str
    provider_message_id: str
    created_at: datetime | None

    @classmethod
    def from_domain(cls, comm: LoadCommunication) -> "LoadCommunicationOut":
        return cls(**comm.__dict__)


class LoadCommunicationPageOut(PageOut[LoadCommunicationOut]):
    pass


class LoadDocumentMetaOut(BaseModel):
    """Deliberately excludes file_path - never a raw storage path in an
    API response (STEP 12)."""

    id: int
    document_type: str
    filename: str
    source: str
    status: str
    created_at: datetime | None

    @classmethod
    def from_domain(cls, doc: LoadDocumentMeta) -> "LoadDocumentMetaOut":
        return cls(**doc.__dict__)
