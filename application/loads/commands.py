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

# mark_load_ready_to_dispatch's outbox idempotency-key time bucket - see
# _driver_dispatch_sms_idempotency_key's docstring for the full rationale
# (content-only keys silently suppress legitimate future resends;
# timestamp-only keys defeat retry dedup; this buckets time coarsely to
# get real dedup within a realistic retry window without permanently
# blocking a later resend).
_RETRY_DEDUPE_WINDOW_SECONDS = 300


def _driver_dispatch_sms_idempotency_key(load_id: int, phone: str, message: str, *, now: float | None = None) -> str:
    """Content-addressed AND time-windowed, deliberately neither alone:

    - Pure content (load_id + phone + message, no time component) was
      this key's first design and had a real bug: two genuinely separate
      Ready-to-Dispatch actions for the same load with byte-identical
      phone/message (e.g. the same driver re-dispatched days later with
      the same standard message text) would collide on the same
      idempotency_key. ON CONFLICT DO NOTHING would then silently drop
      the second, legitimate SMS forever - no error, no event, nothing
      sent, load still marked Ready to Dispatch. That's not
      deduplication, that's silent message loss.
    - Pure timestamp (no content) was rejected per design guidance - it
      defeats retry deduplication entirely, since every resubmission
      (even a genuine same-click retry a second later) gets a fresh key.

    Bucketing time coarsely gets both: a same-content resubmission within
    the window (a client-side retry after a timeout, a double-click) maps
    to the same bucket and is deduped; the same content sent again after
    the window elapses (a legitimate future re-dispatch) gets a new
    bucket and is allowed through. Residual accepted risk: two genuinely
    distinct dispatch actions with byte-identical phone+message occurring
    within the same window would still collide - judged acceptable for a
    ~10-20 driver fleet where that coincidence is vanishingly unlikely,
    and far better than either pure-content (permanent loss) or
    pure-timestamp (no dedup) alone.

    `now` is injectable for tests - defaults to the real clock."""
    import hashlib
    import time

    retry_window = int((now if now is not None else time.time()) // _RETRY_DEDUPE_WINDOW_SECONDS)
    return "driver_dispatch_sms:" + hashlib.sha256(
        f"{load_id}:{phone}:{message}:{retry_window}".encode("utf-8")
    ).hexdigest()


def transition_load(
    load_id: int,
    new_status: str | None,
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

    new_status=None means "assignment only" - apply_transition reapplies
    whatever status is live under its row lock instead of a value read
    by this call's caller before that lock was taken (see
    apply_transition's docstring for why a caller-supplied status here
    would be racy for an assignment-only request).

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


def _update_load_fields_with_event(
    *, load_id: int, updates: dict[str, Any], actor: AuthenticatedActor
) -> None:
    """Phase 9 STEP 8: shared helper for the direct-field-update commands
    below - wraps DispatchDatabaseClient.update_row_fields in an explicit
    transaction() (previously each call opened its own separate
    transaction per internal statement) so the load.updated domain event
    can be recorded in the SAME transaction as the field write (STEP 6).
    A side benefit, not the point of this change: the write and its
    status_events audit row (when status changes) are now also atomic
    with each other, which they were not before."""
    from db_client import DispatchDatabaseClient, transaction
    from realtime.events import publish_event, time_bucketed_key

    with transaction() as conn:
        DispatchDatabaseClient().update_row_fields(load_id, updates, conn=conn, created_by=actor.actor)
        publish_event(
            conn=conn,
            event_type="load.updated",
            aggregate_type="load",
            aggregate_id=str(load_id),
            idempotency_key=time_bucketed_key("load.updated", str(load_id), ",".join(sorted(updates.keys()))),
            actor=actor.actor,
            metadata={"updated_fields": sorted(updates.keys())},
        )


def mark_load_missing_info(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Hold/Need Info, with an optional note."""
    require_permission(actor, Permission.LOAD_EDIT)

    updates: dict[str, Any] = {"Status": "Hold/Need Info"}
    if note:
        updates["Dispatcher Notes"] = note

    _update_load_fields_with_event(load_id=load_id, updates=updates, actor=actor)
    return LoadCommandResult(ok=True, load_id=load_id, status="Hold/Need Info")


def save_load_note(*, actor: AuthenticatedActor, load_id: int, note: str) -> LoadCommandResult:
    """Update Dispatcher Notes only - no status change."""
    require_permission(actor, Permission.LOAD_EDIT)

    _update_load_fields_with_event(load_id=load_id, updates={"Dispatcher Notes": note}, actor=actor)
    return LoadCommandResult(ok=True, load_id=load_id)


def verify_load_booking(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Booking Verified, with an optional note."""
    require_permission(actor, Permission.LOAD_VERIFY)

    updates: dict[str, Any] = {"Status": "Booking Verified"}
    if note:
        updates["Dispatcher Notes"] = note

    _update_load_fields_with_event(load_id=load_id, updates=updates, actor=actor)
    return LoadCommandResult(ok=True, load_id=load_id, status="Booking Verified")


def cancel_load(*, actor: AuthenticatedActor, load_id: int, note: str = "") -> LoadCommandResult:
    """Set a load's status to Cancelled, with an optional note."""
    require_permission(actor, Permission.LOAD_CANCEL)

    updates: dict[str, Any] = {"Status": "Cancelled"}
    if note:
        updates["Dispatcher Notes"] = note

    _update_load_fields_with_event(load_id=load_id, updates=updates, actor=actor)
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

    _update_load_fields_with_event(load_id=load_id, updates=updates, actor=actor)
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
    """Assign driver/truck/chassis, mark the load Ready to Dispatch, and
    queue the driver dispatch SMS for asynchronous delivery. Requires both
    LOAD_READY_TO_DISPATCH and DRIVER_MESSAGE_SEND - this one command does
    both things, so an actor missing either capability is refused before
    anything is written (never a partial action).

    Phase 6 change (see docs/architecture/OUTBOX.md): this used to call
    Twilio synchronously and only wrote the database if the send
    succeeded - the external call and the database writes were never
    atomic with each other, but were at least ordered so a failed SMS
    left zero mutation. That ordering is gone: the business-state write
    (driver/truck/chassis/status), the dispatch_messages audit row, and
    the outbox event are now one atomic database transaction, and the SMS
    itself is sent afterward, asynchronously, by
    services.outbox_processor. A load can now show "Ready to Dispatch"
    before its SMS is confirmed delivered - `sms_status="queued"` on the
    result reflects that honestly (see ReadyToDispatchResult's
    docstring), and dispatch_messages.delivery_status/outbox_events let an
    operator see actual delivery state. Deliberately not gated on SMS
    success: this is the whole point of decoupling the business
    transaction from the external side effect, not an oversight.

    An invalid phone number still fails fast with zero mutation and zero
    enqueue - that's local validation, not an external call, so there's
    no reason to make it asynchronous."""
    require_permission(actor, Permission.LOAD_READY_TO_DISPATCH)
    require_permission(actor, Permission.DRIVER_MESSAGE_SEND)

    from services.driver_sms_service import format_phone_e164

    normalized_phone = format_phone_e164(phone)
    if not normalized_phone:
        return ReadyToDispatchResult(
            ok=False, load_id=load_id, reason=f"'{phone.strip()}' isn't a valid phone number."
        )

    from db_client import DispatchDatabaseClient, transaction
    from repositories.outbox_repo import enqueue_outbox_event
    from services.dispatch_data_service import _insert_dispatch_message

    idempotency_key = _driver_dispatch_sms_idempotency_key(load_id, normalized_phone, message)

    with transaction() as conn:
        DispatchDatabaseClient().update_row_fields(
            load_id,
            {
                "Driver Name": driver_name.strip(),
                "Truck Assigned": truck.strip(),
                "Chassis": chassis.strip(),
                "Status": "Ready to Dispatch",
                "Dispatcher Notes": note or "Driver, truck, and chassis assigned. Ready to dispatch.",
            },
            conn=conn,
            created_by=actor.actor,
        )

        dispatch_message_id = _insert_dispatch_message(
            load_id,
            "driver_dispatch_sms",
            "outbound",
            normalized_phone,
            message,
            provider="twilio",
            sent_by=actor.actor,
            conn=conn,
            delivery_status="pending_delivery",
        )

        enqueue_outbox_event(
            conn=conn,
            event_type="driver_dispatch_sms",
            aggregate_type="load",
            aggregate_id=str(load_id),
            payload={"to": normalized_phone, "message": message, "dispatch_message_id": dispatch_message_id},
            idempotency_key=idempotency_key,
            actor=actor.actor,
        )

        # Phase 9 STEP 11: communication.queued, in the same transaction
        # as the outbox enqueue it describes. Keyed on dispatch_message_id
        # (fresh per call, unique per actual insert) rather than a
        # time-bucketed key - no "legitimate resend" ambiguity to guard
        # against here, since a genuine retry of this whole command hits
        # the outbox's own idempotency_key first and produces the same
        # dispatch_message_id only if the caller passed one through
        # (it doesn't - each call inserts a fresh dispatch_messages row).
        from realtime.events import publish_event

        publish_event(
            conn=conn,
            event_type="communication.queued",
            aggregate_type="dispatch_message",
            aggregate_id=str(dispatch_message_id),
            idempotency_key=f"communication.queued:{dispatch_message_id}",
            actor=actor.actor,
            metadata={"load_id": load_id, "dispatch_message_id": dispatch_message_id},
        )

    return ReadyToDispatchResult(ok=True, load_id=load_id, status="Ready to Dispatch", sms_status="queued")
