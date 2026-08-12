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
