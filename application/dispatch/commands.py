# application/dispatch/commands.py

from __future__ import annotations

"""Dispatch Board mutation commands - previously
pages_app/dispatch_board.py called DispatchDatabaseClient/
dispatch_transition_service/dispatch_data_service directly from inside
Streamlit button handlers with no authorization check. Every command
here requires Permission.DISPATCH_TRANSITION before writing.

Status transitions themselves (Save Status Update's status-change path)
are NOT handled here - application.loads.commands.transition_load is the
one canonical command wrapping dispatch_transition_service.apply_transition
(reused by both pages_app/dispatch_board.py and api/routers/loads.py as
of the Phase 5B closure pass, converging what were briefly two parallel
implementations onto one)."""

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.dispatch.models import DispatchTransitionResult


def update_dispatch_assignment(*, actor: AuthenticatedActor, load_id: int, updates: dict[str, Any]) -> None:
    """Driver/Truck/Chassis-only update with no status change (the
    operational Status tab's pre-transition assignment write)."""
    require_permission(actor, Permission.DISPATCH_TRANSITION)

    if not updates:
        return

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)


def apply_legacy_status_update(*, actor: AuthenticatedActor, load_id: int, updates: dict[str, Any]) -> None:
    """The legacy (pre-dispatch) status tab's direct write - status,
    driver/truck/chassis, and notes all in one update_row_fields call,
    bypassing apply_transition's validate_transition() checks. This
    predates Phase 5B and is not fixed here (see docs/AUTHENTICATION.md's
    known-gaps note) - this command only adds the permission check in
    front of the existing behavior, unchanged otherwise."""
    require_permission(actor, Permission.DISPATCH_TRANSITION)

    if not updates:
        return

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(load_id, updates, created_by=actor.actor)


def save_dispatch_progress(
    *,
    actor: AuthenticatedActor,
    load_id: int,
    current_location: str,
    eta_value: Any,
    live_load_status: str,
    live_unload_status: str,
) -> None:
    require_permission(actor, Permission.DISPATCH_TRANSITION)

    from services.dispatch_data_service import _update_load_extra_fields

    _update_load_extra_fields(load_id, current_location, eta_value, live_load_status, live_unload_status)


def mark_load_ready_for_billing(*, actor: AuthenticatedActor, load_id: int) -> DispatchTransitionResult:
    """The "Mark Ready for ProfitTools" action - hands a load off from
    dispatch to the billing pipeline. Requires DISPATCH_TRANSITION (a
    dispatch-side action performed by dispatch/manager/admin), not a
    billing permission - accounting does not initiate this handoff."""
    require_permission(actor, Permission.DISPATCH_TRANSITION)

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().update_row_fields(
        load_id, {"Status": "Ready for ProfitTools"}, created_by=actor.actor
    )
    return DispatchTransitionResult(ok=True, reason="", status="Ready for ProfitTools", closeout_stage="")


def log_dispatch_communication(
    *,
    actor: AuthenticatedActor,
    load_id: int,
    message_type: str,
    direction: str,
    recipient: str,
    message_body: str,
    provider: str = "internal",
) -> None:
    """Covers the 5 remaining Dispatch Board note/message-logging buttons
    (Save Message, Copy/Paste Ready, Save Driver Communication, Save
    Customer Note, Save Operational Note) - all write to dispatch_messages
    via services.dispatch_data_service._insert_dispatch_message. A
    distinct permission from DRIVER_MESSAGE_SEND (Phase 5's
    mark_load_ready_to_dispatch, which actually sends an SMS through
    Twilio) - this is internal logging with no external side effect."""
    require_permission(actor, Permission.DISPATCH_COMMUNICATION_LOG)

    from services.dispatch_data_service import _insert_dispatch_message

    _insert_dispatch_message(
        load_id, message_type, direction, recipient, message_body, provider=provider, sent_by=actor.actor
    )
