from __future__ import annotations

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.exceptions import CommandFailedError, ConflictError, NotFoundError, ValidationError
from application.loads.models import (
    CreateLoadResult,
    LoadCommandResult,
    ReadyToDispatchResult,
    TransitionResult,
    UpdateLoadResult,
)
from utils.error_sanitizer import sanitize_exception_message


def transition_load(
    load_id: int,
    new_status: str,
    *,
    actor: AuthenticatedActor,
    note: str = "",
    driver: str | None = None,
    truck: str | None = None,
    override: bool = False,
    override_reason: str = "",
) -> TransitionResult:
    """Status transition + optional driver/truck assignment. The one
    canonical application command wrapping
    services.dispatch_transition_service.apply_transition - called by
    both api/routers/loads.py and pages_app/dispatch_board.py (Phase 5B
    converged these onto a single implementation instead of the two
    that briefly existed in parallel - see application/dispatch/
    commands.py's history).

    Requires Permission.DISPATCH_TRANSITION before doing anything else -
    an unauthorized actor triggers zero reads/writes.

    apply_transition is the one function allowed to change loads.status
    and runs its assignment/status/closeout writes and audit rows in a
    single db_client.transaction() (see
    tests/test_dispatch_transition_service.py for the atomicity tests).
    The real actor identity is threaded into that transaction's audit
    rows via actor_display_name - see apply_transition's own docstring.

    Raises NotFoundError / ConflictError instead of returning ok=False
    with a 200-shaped result - a caller like the API needs a distinct
    status code for "load not found" (404) vs. "transition not valid from
    the current state" (409), not a 200 response it has to inspect an
    `ok` flag on to find out something failed."""
    require_permission(actor, Permission.DISPATCH_TRANSITION)

    from services.dispatch_transition_service import apply_transition

    result = apply_transition(
        load_id,
        new_status,
        note=note,
        driver=driver,
        truck=truck,
        override=override,
        override_reason=override_reason,
        actor_display_name=actor.actor,
    )

    if not result.get("ok"):
        reason = str(result.get("reason") or "")
        if "not found" in reason.lower():
            raise NotFoundError(reason)
        raise ConflictError(reason)

    return TransitionResult(
        ok=True,
        reason="",
        status=str(result.get("status") or ""),
        closeout_stage=str(result.get("closeout_stage") or ""),
    )


def create_load_from_work_item(
    work_item_id: int, approved_fields: dict[str, Any], *, actor: AuthenticatedActor, **kwargs: Any
) -> CreateLoadResult:
    """Create a load from a reviewed Operations Inbox draft.

    Requires Permission.WORK_ITEM_MANAGE before doing anything else - an
    unauthorized actor triggers zero reads/writes (Stage 2 closure pass).

    KNOWN LIMITATION (documented, not silently accepted): this delegates to
    services.operations_inbox_service.create_load_from_inbox_item, which
    itself makes several separate, non-atomic writes (create the load,
    insert its document row, update order_intake twice under different
    column sets, save the communication row, update the case). Phase 1
    intentionally reuses this canonical, certification-tested workflow
    rather than re-implementing multi-container/duplicate-protection
    business rules here - making the whole chain a single transaction is
    scoped to Phase 2 (see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md,
    "Known limitations").

    Lazy-imported: services.operations_inbox_service imports streamlit at
    module top, so importing it happens inside this function body rather
    than at application/ module load time.
    """
    require_permission(actor, Permission.WORK_ITEM_MANAGE)

    from services.operations_inbox_service import create_load_from_inbox_item

    if not approved_fields.get("Booking Number") and not approved_fields.get("Reference Number"):
        raise ValidationError("Booking Number or Reference Number is required to create a load.")

    try:
        result = create_load_from_inbox_item(int(work_item_id), approved_fields, **kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced as a structured application error
        raise CommandFailedError(sanitize_exception_message(exc)) from exc

    return CreateLoadResult(
        ok=True,
        load_id=result.get("load_id"),
        review_status=str(result.get("review_status") or ""),
    )


def update_load_from_work_item(
    work_item_id: int,
    load_id: int,
    approved_fields: dict[str, Any],
    *,
    actor: AuthenticatedActor,
    **kwargs: Any,
) -> UpdateLoadResult:
    """Apply an existing-load update from an Operations Inbox work item.

    Requires Permission.WORK_ITEM_MANAGE before doing anything else - an
    unauthorized actor triggers zero reads/writes (Stage 2 closure pass).

    Same known limitation as create_load_from_work_item: delegates to
    services.operations_inbox_service.update_load_from_inbox_item, which
    is not yet single-transaction atomic. See docs/architecture/
    BACKEND_BOUNDARY_PHASE_1.md."""
    require_permission(actor, Permission.WORK_ITEM_MANAGE)

    from services.operations_inbox_service import update_load_from_inbox_item

    try:
        result = update_load_from_inbox_item(int(work_item_id), int(load_id), approved_fields, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise CommandFailedError(sanitize_exception_message(exc)) from exc

    if result.get("error"):
        raise NotFoundError(str(result["error"]))

    return UpdateLoadResult(
        ok=True,
        load_id=result.get("load_id"),
        updated_fields=list(result.get("updated_fields") or []),
        skipped_fields=list(result.get("skipped_fields") or []),
    )


# ---------------------------------------------------------------------------
# Phase 5: Orders/Load Management command boundary.
#
# Each command below wraps a mutation that pages_app/orders_management.py
# (and, for mark_load_ready_to_dispatch, its driver-SMS side effect) used to
# perform directly against DispatchDatabaseClient/services with no
# authorization beyond "did this actor's role get past the page-visibility
# check". require_permission() is called first, before any read or write
# these functions perform - an unauthorized actor causes zero mutation, not
# a partial one. Behavior/side-effects are otherwise preserved exactly as
# they were in orders_management.py; the UI-facing default note/message text
# stays a caller (Streamlit) concern - these commands accept whatever text
# the caller computed rather than hardcoding UI copy.
# ---------------------------------------------------------------------------


def mark_load_missing_info(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Hold/Need Info, with an optional note."""
    require_permission(actor, Permission.LOAD_EDIT)

    from db_client import DispatchDatabaseClient

    updates: dict[str, Any] = {"Status": "Hold/Need Info"}
    if note:
        updates["Dispatcher Notes"] = note

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)
    return LoadCommandResult(ok=True, load_id=load_id, status="Hold/Need Info")


def save_load_note(*, actor: AuthenticatedActor, load_id: int, note: str) -> LoadCommandResult:
    """Update Dispatcher Notes only - no status change."""
    require_permission(actor, Permission.LOAD_EDIT)

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(
        load_id, {"Dispatcher Notes": note}, created_by=actor.actor
    )
    return LoadCommandResult(ok=True, load_id=load_id)


def verify_load_booking(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Booking Verified, with an optional note."""
    require_permission(actor, Permission.LOAD_VERIFY)

    from db_client import DispatchDatabaseClient

    updates: dict[str, Any] = {"Status": "Booking Verified"}
    if note:
        updates["Dispatcher Notes"] = note

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)
    return LoadCommandResult(ok=True, load_id=load_id, status="Booking Verified")


def cancel_load(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Cancelled, with an optional note."""
    require_permission(actor, Permission.LOAD_CANCEL)

    from db_client import DispatchDatabaseClient

    updates: dict[str, Any] = {"Status": "Cancelled"}
    if note:
        updates["Dispatcher Notes"] = note

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)
    return LoadCommandResult(ok=True, load_id=load_id, status="Cancelled")


def update_load_fields(*, actor: AuthenticatedActor, load_id: int, updates: dict[str, Any]) -> LoadCommandResult:
    """Free-form multi-field save (the Booking Review / Order Detail Editor
    forms). `updates` is passed through to DispatchDatabaseClient.
    update_row_fields() unchanged - that function is the existing
    allowlist/validation boundary for which columns may be written; this
    command adds the permission check in front of it, nothing else."""
    require_permission(actor, Permission.LOAD_EDIT)

    if not updates:
        raise ValidationError("No fields supplied to update.")

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)
    return LoadCommandResult(ok=True, load_id=load_id, status=str(updates.get("status") or updates.get("Status") or ""))


def mark_load_ready_to_dispatch(
    *,
    actor: AuthenticatedActor,
    load_id: int,
    driver_name: str,
    truck: str,
    chassis: str,
    phone: str,
    message: str,
    note: str = "",
) -> ReadyToDispatchResult:
    """Send the driver dispatch SMS, log it, and mark the load Ready to
    Dispatch. Requires both LOAD_READY_TO_DISPATCH and DRIVER_MESSAGE_SEND
    - this one command does both things, so an actor missing either
    capability is refused before the SMS is sent (never a partial action:
    SMS-sent-but-not-authorized-to-dispatch, or vice versa).

    Preserves the original UI logic's ordering and failure handling
    exactly: an invalid phone number or a failed send returns ok=False
    with zero database mutation - the SMS provider call is not
    transactional with the database writes (external side effect), so
    this was never atomic and remains not atomic, matching the pre-Phase-5
    behavior it replaces."""
    require_permission(actor, Permission.LOAD_READY_TO_DISPATCH)
    require_permission(actor, Permission.DRIVER_MESSAGE_SEND)

    from services.driver_sms_service import format_phone_e164, send_sms

    normalized_phone = format_phone_e164(phone)
    if not normalized_phone:
        return ReadyToDispatchResult(
            ok=False, load_id=load_id, reason=f"'{phone.strip()}' isn't a valid phone number."
        )

    sent, sid_or_error = send_sms(normalized_phone, message)
    if not sent:
        return ReadyToDispatchResult(ok=False, load_id=load_id, reason=str(sid_or_error))

    from db_client import DispatchDatabaseClient
    from services.dispatch_data_service import _insert_dispatch_message

    _insert_dispatch_message(
        load_id,
        "driver_dispatch_sms",
        "outbound",
        normalized_phone,
        message,
        provider="twilio",
        sent_by=actor.actor,
    )
    DispatchDatabaseClient().update_row_fields(
        load_id,
        {
            "Driver Name": driver_name.strip(),
            "Truck Assigned": truck.strip(),
            "Chassis": chassis.strip(),
            "Status": "Ready to Dispatch",
            "Dispatcher Notes": note or "Driver, truck, and chassis assigned. Ready to dispatch.",
        },
        created_by=actor.actor,
    )

    return ReadyToDispatchResult(ok=True, load_id=load_id, status="Ready to Dispatch", sms_sent=True)
