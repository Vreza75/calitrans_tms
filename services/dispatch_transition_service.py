from __future__ import annotations

import pandas as pd

from db_client import DispatchDatabaseClient, execute, read_df
from services.dispatch_stages import COMPLETION_STATUS, validate_transition
from services.workflow_constants import normalize_service_flow


def _load_row(load_id: int) -> pd.DataFrame:
    return read_df(
        """
        select id as _row_id, type as "TYPE", status as "Status",
               driver_name as "Driver Name", truck_assigned as "Truck Assigned",
               port as "Port", warehouse as "Warehouse",
               empty_return_location, dispatcher_notes as "Dispatcher Notes",
               coalesce(closeout_stage, 'Not Started') as closeout_stage
        from loads
        where id = :load_id
        """,
        {"load_id": load_id},
    )


def _update_load(load_id: int, updates: dict) -> None:
    DispatchDatabaseClient().update_row_fields(load_id, updates)


def _set_closeout_stage(load_id: int, closeout_stage: str) -> None:
    execute(
        "update loads set closeout_stage = :closeout_stage where id = :load_id",
        {"load_id": load_id, "closeout_stage": closeout_stage},
    )


def apply_transition(
    load_id: int,
    new_status: str,
    *,
    note: str = "",
    override: bool = False,
    override_reason: str = "",
) -> dict:
    """Validate and apply an operational status transition for one load.

    This is the only function allowed to change loads.status going
    forward. It reuses DispatchDatabaseClient.update_row_fields(), which
    already inserts a status_events audit row whenever status changes —
    that mechanism is not duplicated here.
    """
    if override and not override_reason.strip():
        return {"ok": False, "reason": "An override requires a reason.", "status": "", "closeout_stage": ""}

    df = _load_row(load_id)
    if df.empty:
        return {"ok": False, "reason": f"Load {load_id} not found.", "status": "", "closeout_stage": ""}

    row = df.iloc[0]
    move_type = normalize_service_flow(str(row.get("TYPE", "")), default="Local Import")
    current_status = str(row.get("Status", "") or "New")
    has_driver = bool(str(row.get("Driver Name", "") or "").strip())
    has_truck = bool(str(row.get("Truck Assigned", "") or "").strip())
    has_origin = bool(str(row.get("Port", "") or row.get("Warehouse", "") or "").strip())
    empty_return_required = bool(str(row.get("empty_return_location", "") or "").strip())

    ok, reason = validate_transition(
        move_type,
        current_status,
        new_status,
        has_driver=has_driver,
        has_truck=has_truck,
        has_origin=has_origin,
        empty_return_required=empty_return_required,
        override=override,
    )

    if not ok:
        return {"ok": False, "reason": reason, "status": current_status, "closeout_stage": str(row.get("closeout_stage", "Not Started"))}

    updates: dict = {"Status": new_status}
    final_note = note.strip()
    if override:
        final_note = f"{final_note} [override: {override_reason.strip()}]".strip()
    if final_note:
        updates["Dispatcher Notes"] = final_note

    _update_load(load_id, updates)

    closeout_stage = str(row.get("closeout_stage", "Not Started") or "Not Started")
    if new_status == COMPLETION_STATUS and closeout_stage == "Not Started":
        closeout_stage = "POD Needed"
        _set_closeout_stage(load_id, closeout_stage)

    return {"ok": True, "reason": "", "status": new_status, "closeout_stage": closeout_stage}
