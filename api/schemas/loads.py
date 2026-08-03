from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from application.loads.models import LoadSummary


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
    updated_at: Any

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
