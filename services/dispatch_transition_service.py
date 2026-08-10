from __future__ import annotations

from sqlalchemy import text

from db_client import DispatchDatabaseClient, transaction
from services.dispatch_stages import COMPLETION_STATUS, validate_transition
from services.workflow_constants import normalize_service_flow


def _load_row_for_update(load_id: int, *, conn) -> dict | None:
    """Read the load row locked (`SELECT ... FOR UPDATE`) inside the same
    transaction/connection the rest of apply_transition writes through.

    Phase 1 correction (Codex finding): the row used to be read via a
    separate, unlocked read_df() call *before* the transaction opened, so
    the status validate_transition() checked against could already be
    stale by the time the write happened - two dispatchers racing to
    transition the same load could both pass validation against the same
    pre-transition status. Locking the row here blocks a second concurrent
    apply_transition() on the same load_id until this one commits or rolls
    back, so the status it validates against is guaranteed current."""
    row = conn.execute(
        text(
            """
            select id as _row_id, type as "TYPE", status as "Status",
                   driver_name as "Driver Name", truck_assigned as "Truck Assigned",
                   port as "Port", warehouse as "Warehouse",
                   empty_return_location, dispatcher_notes as "Dispatcher Notes",
                   coalesce(closeout_stage, 'Not Started') as closeout_stage
            from loads
            where id = :load_id
            for update
            """
        ),
        {"load_id": load_id},
    ).mappings().first()
    return dict(row) if row is not None else None


def _update_load(load_id: int, updates: dict, *, conn) -> None:
    DispatchDatabaseClient().update_row_fields(load_id, updates, conn=conn)


def _set_closeout_stage(load_id: int, closeout_stage: str, *, conn) -> None:
    conn.execute(
        text("update loads set closeout_stage = :closeout_stage where id = :load_id"),
        {"load_id": load_id, "closeout_stage": closeout_stage},
    )


def _insert_assignment_audit(load_id: int, current_status: str, notes: str, *, conn) -> None:
    """Driver/truck assignment gets its own status_events row, distinct
    from the status-change row — old_status == new_status == the load's
    status at the time of assignment, so this reads clearly as an
    assignment event rather than a fake status transition."""
    conn.execute(
        text(
            """
            insert into status_events (load_id, old_status, new_status, notes, created_by)
            values (:load_id, :status, :status, :notes, 'dispatcher')
            """
        ),
        {"load_id": load_id, "status": current_status, "notes": notes},
    )


def apply_transition(
    load_id: int,
    new_status: str,
    *,
    note: str = "",
    driver: str | None = None,
    truck: str | None = None,
    override: bool = False,
    override_reason: str = "",
) -> dict:
    """Validate and apply an operational status transition for one load.

    This is the only function allowed to change loads.status going
    forward. Driver/truck assignment (when provided) is written and
    audited as its own event, separate from the status-change event —
    assignment is data, not a board stage.

    The row read, transition validation, and all writes now happen inside
    one transaction with the row locked FOR UPDATE (see
    _load_row_for_update) - validation can no longer run against a status
    a concurrent transition is about to change out from under it.
    """
    if override and not override_reason.strip():
        return {"ok": False, "reason": "An override requires a reason.", "status": "", "closeout_stage": ""}

    with transaction() as conn:
        row = _load_row_for_update(load_id, conn=conn)
        if row is None:
            return {"ok": False, "reason": f"Load {load_id} not found.", "status": "", "closeout_stage": ""}

        move_type = normalize_service_flow(str(row.get("TYPE", "")), default="Local Import")
        current_status = str(row.get("Status", "") or "New")
        existing_driver = str(row.get("Driver Name", "") or "").strip()
        existing_truck = str(row.get("Truck Assigned", "") or "").strip()

        effective_driver = driver.strip() if driver and driver.strip() else existing_driver
        effective_truck = truck.strip() if truck and truck.strip() else existing_truck
        has_origin = bool(str(row.get("Port", "") or row.get("Warehouse", "") or "").strip())
        empty_return_required = bool(str(row.get("empty_return_location", "") or "").strip())

        ok, reason = validate_transition(
            move_type,
            current_status,
            new_status,
            has_driver=bool(effective_driver),
            has_truck=bool(effective_truck),
            has_origin=has_origin,
            empty_return_required=empty_return_required,
            override=override,
        )

        if not ok:
            return {
                "ok": False,
                "reason": reason,
                "status": current_status,
                "closeout_stage": str(row.get("closeout_stage", "Not Started")),
            }

        assignment_updates = {}
        if driver and driver.strip() and driver.strip() != existing_driver:
            assignment_updates["Driver Name"] = driver.strip()
        if truck and truck.strip() and truck.strip() != existing_truck:
            assignment_updates["Truck Assigned"] = truck.strip()

        status_updates: dict = {"Status": new_status}
        final_note = note.strip()
        if override:
            final_note = f"{final_note} [override: {override_reason.strip()}]".strip()
        if final_note:
            status_updates["Dispatcher Notes"] = final_note

        closeout_stage = str(row.get("closeout_stage", "Not Started") or "Not Started")
        should_set_closeout = new_status == COMPLETION_STATUS and closeout_stage == "Not Started"

        if assignment_updates:
            _update_load(load_id, assignment_updates, conn=conn)
            parts = []
            if "Driver Name" in assignment_updates:
                parts.append(f"Driver assigned: {assignment_updates['Driver Name']}")
            if "Truck Assigned" in assignment_updates:
                parts.append(f"Truck assigned: {assignment_updates['Truck Assigned']}")
            _insert_assignment_audit(load_id, current_status, "; ".join(parts), conn=conn)

        _update_load(load_id, status_updates, conn=conn)

        if should_set_closeout:
            closeout_stage = "POD Needed"
            _set_closeout_stage(load_id, closeout_stage, conn=conn)

    return {"ok": True, "reason": "", "status": new_status, "closeout_stage": closeout_stage}
