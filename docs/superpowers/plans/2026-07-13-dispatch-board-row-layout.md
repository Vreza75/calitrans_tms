# Dispatch Board Row Layout & Shared Canonical Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/superpowers/specs/2026-07-12-dispatch-board-row-layout-design.md` — collapse the canonical status model to 7 shared stages (no more "Driver Assigned" as a stage, no more move-type-specific canonical values), add a cosmetic display-label layer, and replace the multi-column board with a row-based layout built from native Streamlit components (no raw multi-line HTML, per the bug fixed earlier this session).

**Architecture:** `dispatch_stages.py`, `dispatch_transition_service.py`, `dispatch_legacy_status.py`, and `dispatch_board_view.py` are rewritten in place (same module names, same public function names where possible, different internals) since nothing outside `pages_app/dispatch_board.py` depends on them yet (confirmed — these are all new this session). The UI layer (`pages_app/dispatch_board.py`) is updated last, once the backend layer's new shape is locked in and tested.

**Tech Stack:** Python, pandas, Streamlit native components (`st.columns`, `st.expander`, `st.button`, `st.caption`) — no HTML except short single-line badges reusing the existing `render_status_badge`/exception-badge pattern already proven safe this session.

## Global Constraints

- `loads.status` stores exactly one of: `Ready to Dispatch`, `En Route to Pickup`, `At Pickup`, `En Route to Delivery`, `At Delivery`, `Returning Empty` (Import only), `Completed`, `Cancelled`. No other value is valid going forward.
- Display labels are computed at render time only, never written to the database.
- Driver/truck assignment writes are a separate `status_events` audit entry from the status-change entry, even when both happen in the same `apply_transition()` call (per the request's explicit requirement to preserve assignment history without conflating it with a status milestone).
- No multi-line HTML `style="..."` attributes anywhere in new code — every `st.markdown(..., unsafe_allow_html=True)` call must be single-line, matching the fix applied earlier this session.
- Do not touch `services/dispatch_workflow_service.py`, `services/workflow_status.py`, or anything in Orders Management / Active Status / Billing / Operations Inbox.

---

### Task 1: Rewrite `dispatch_stages.py` — shared canonical stages

**Files:**
- Modify: `services/dispatch_stages.py` (full rewrite)
- Modify: `tests/test_dispatch_stages.py` (full rewrite)

**Interfaces:**
- Produces: `SHARED_STAGES: list[str]`, `COMPLETION_STATUS = "Completed"`, `CANCELLED_STATUS = "Cancelled"`, `get_operational_stages(move_type: str) -> list[str]`, `validate_transition(move_type, current_status, new_status, *, has_driver=False, has_truck=False, has_origin=False, empty_return_required=False, override=False) -> tuple[bool, str]`. Task 2 imports all of these.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_dispatch_stages.py` with:
```python
from services.dispatch_stages import (
    CANCELLED_STATUS,
    COMPLETION_STATUS,
    get_operational_stages,
    validate_transition,
)


def test_import_includes_returning_empty():
    assert get_operational_stages("Import") == [
        "Ready to Dispatch",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Returning Empty",
        "Completed",
    ]


def test_export_excludes_returning_empty():
    assert get_operational_stages("Export") == [
        "Ready to Dispatch",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Completed",
    ]


def test_local_import_and_export_share_stage_list():
    assert get_operational_stages("Local Import") == get_operational_stages("Local Export")
    assert "Returning Empty" not in get_operational_stages("Local Import")


def test_cannot_start_en_route_to_pickup_without_driver():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=False, has_truck=True, has_origin=True)
    assert ok is False
    assert "driver" in reason.lower()


def test_cannot_start_en_route_to_pickup_without_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=False, has_origin=True)
    assert ok is False


def test_can_start_en_route_to_pickup_with_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_cannot_go_en_route_without_origin():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=False)
    assert ok is False
    assert "origin" in reason.lower()


def test_cannot_reach_at_pickup_before_en_route_to_pickup():
    ok, reason = validate_transition("Export", "Ready to Dispatch", "At Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is False


def test_cannot_reach_at_delivery_before_en_route_to_delivery():
    ok, reason = validate_transition("Import", "At Pickup", "At Delivery", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is False


def test_import_cannot_return_empty_before_at_delivery():
    ok, reason = validate_transition("Import", "En Route to Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False
    assert "at delivery" in reason.lower()


def test_import_can_return_empty_after_at_delivery():
    ok, reason = validate_transition("Import", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_export_cannot_return_empty_at_all():
    ok, reason = validate_transition("Export", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_import_complete_requires_returning_empty_when_required():
    ok, reason = validate_transition("Import", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True, empty_return_required=True)
    assert ok is False


def test_import_complete_ok_from_at_delivery_when_not_required():
    ok, reason = validate_transition("Import", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True, empty_return_required=False)
    assert ok is True


def test_export_complete_ok_from_at_delivery():
    ok, reason = validate_transition("Export", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_completed_load_blocks_further_transitions():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_completed_load_allows_transition_with_override():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is True


def test_cancel_allowed_from_active_status():
    ok, reason = validate_transition("Import", "En Route to Pickup", CANCELLED_STATUS)
    assert ok is True


def test_cannot_cancel_a_completed_load():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, CANCELLED_STATUS)
    assert ok is False


def test_backward_transition_blocked_without_override():
    ok, reason = validate_transition("Import", "At Pickup", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_backward_transition_allowed_with_override():
    ok, reason = validate_transition("Import", "At Pickup", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is True


def test_unknown_new_status_rejected():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Not A Real Status")
    assert ok is False


def test_returning_empty_rejected_as_target_for_export():
    ok, reason = validate_transition("Export", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_stages.py -v`
Expected: FAIL (old module shape doesn't match — `get_operational_stages("Import")` currently returns 11 items, not 7).

- [ ] **Step 3: Rewrite `services/dispatch_stages.py`**

```python
from __future__ import annotations

from services.workflow_constants import normalize_service_flow

COMPLETION_STATUS = "Completed"
CANCELLED_STATUS = "Cancelled"

SHARED_STAGES = [
    "Ready to Dispatch",
    "En Route to Pickup",
    "At Pickup",
    "En Route to Delivery",
    "At Delivery",
    "Returning Empty",
    "Completed",
]

_MOVE_TYPES_WITH_EMPTY_RETURN = {"Import"}


def get_operational_stages(move_type: str) -> list[str]:
    normalized = normalize_service_flow(move_type, default="Local Import")
    if normalized in _MOVE_TYPES_WITH_EMPTY_RETURN:
        return list(SHARED_STAGES)
    return [stage for stage in SHARED_STAGES if stage != "Returning Empty"]


def _stage_index(stages: list[str], status: str) -> int | None:
    try:
        return stages.index(status)
    except ValueError:
        return None


def validate_transition(
    move_type: str,
    current_status: str,
    new_status: str,
    *,
    has_driver: bool = False,
    has_truck: bool = False,
    has_origin: bool = False,
    empty_return_required: bool = False,
    override: bool = False,
) -> tuple[bool, str]:
    """Return (is_valid, reason). reason is "" when valid.

    Hard business rules (driver+truck required to start moving, an origin
    required to move, "At X" requires having passed through "En Route to
    X" first, Returning Empty requires At Delivery, Completed requires the
    move type's milestone) always apply, override or not. override only
    bypasses the completed-load lock and the generic forward-skip /
    backward-move sequencing guard.
    """
    stages = get_operational_stages(move_type)

    if new_status == CANCELLED_STATUS:
        if current_status == COMPLETION_STATUS:
            return False, "Cannot cancel a load that is already Completed."
        return True, ""

    if new_status not in stages:
        return False, f"'{new_status}' is not a valid operational status for {move_type}."

    if current_status in (COMPLETION_STATUS, CANCELLED_STATUS) and not override:
        return False, f"Load is {current_status}; further operational status changes require an override."

    current_index = _stage_index(stages, current_status)
    new_index = stages.index(new_status)

    if new_status == "En Route to Pickup" and not (has_driver and has_truck):
        return False, "Driver and truck must be assigned before starting En Route to Pickup."

    if new_status.startswith("En Route") and not has_origin:
        return False, f"Cannot move to '{new_status}' without a valid origin."

    _preceding_enroute = {"At Pickup": "En Route to Pickup", "At Delivery": "En Route to Delivery"}
    if new_status in _preceding_enroute:
        required = _preceding_enroute[new_status]
        required_index = stages.index(required)
        if current_index is None or current_index < required_index:
            return False, f"Cannot move to '{new_status}' before '{required}'."

    if new_status == "Returning Empty":
        if "Returning Empty" not in stages:
            return False, "Returning Empty does not apply to this move type."
        delivery_index = stages.index("At Delivery")
        if current_index is None or current_index < delivery_index:
            return False, "Cannot start empty return before the load has reached At Delivery."

    if new_status == COMPLETION_STATUS:
        if empty_return_required and "Returning Empty" in stages:
            milestone = "Returning Empty"
        else:
            milestone = "At Delivery"
        milestone_index = stages.index(milestone)
        if current_index is None or current_index < milestone_index:
            return False, f"Cannot mark Completed before reaching '{milestone}'."

    if not override and current_index is not None and new_status != COMPLETION_STATUS:
        if new_index > current_index + 1:
            return False, f"Cannot skip from '{current_status}' directly to '{new_status}' without an override."
        if new_index < current_index:
            return False, f"Cannot move backward from '{current_status}' to '{new_status}' without an override."

    return True, ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_stages.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_stages.py tests/test_dispatch_stages.py
git commit -m "Collapse dispatch stages to a shared 7-stage canonical model, remove Assigned as a stage"
```

---

### Task 2: Rewrite `dispatch_transition_service.py` — driver/truck as separate audit event

**Files:**
- Modify: `services/dispatch_transition_service.py` (full rewrite)
- Modify: `tests/test_dispatch_transition_service.py` (full rewrite)

**Interfaces:**
- Produces: `apply_transition(load_id: int, new_status: str, *, note: str = "", driver: str | None = None, truck: str | None = None, override: bool = False, override_reason: str = "") -> dict` returning `{"ok": bool, "reason": str, "status": str, "closeout_stage": str}`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_dispatch_transition_service.py` with:
```python
import pandas as pd
import pytest

from services import dispatch_transition_service as svc


class _FakeDb:
    def __init__(self, load: dict):
        self.load = dict(load)
        self.update_calls = []
        self.closeout_calls = []
        self.audit_notes = []

    def read_load(self, load_id: int) -> pd.DataFrame:
        return pd.DataFrame([self.load])

    def update_row_fields(self, load_id: int, updates: dict) -> None:
        self.update_calls.append(dict(updates))
        self.load.update(updates)

    def set_closeout_stage(self, load_id: int, closeout_stage: str) -> None:
        self.closeout_calls.append(closeout_stage)
        self.load["closeout_stage"] = closeout_stage

    def insert_assignment_audit(self, load_id: int, current_status: str, notes: str) -> None:
        self.audit_notes.append(notes)


@pytest.fixture
def import_load():
    return {
        "_row_id": 1,
        "TYPE": "Import",
        "Status": "Ready to Dispatch",
        "Driver Name": "",
        "Truck Assigned": "",
        "Port": "Bayport",
        "empty_return_location": "",
        "closeout_stage": "Not Started",
    }


def _wire(fake, monkeypatch):
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.set_closeout_stage)
    monkeypatch.setattr(svc, "_insert_assignment_audit", fake.insert_assignment_audit)


def test_assign_and_start_writes_driver_truck_and_status_in_separate_calls(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup", driver="Alex", truck="T1")

    assert result["ok"] is True
    assert result["status"] == "En Route to Pickup"
    assert {"Driver Name": "Alex", "Truck Assigned": "T1"} in fake.update_calls
    assert {"Status": "En Route to Pickup"} in fake.update_calls
    assert len(fake.audit_notes) == 1
    assert "Alex" in fake.audit_notes[0]


def test_start_en_route_with_existing_driver_needs_no_assignment_write(monkeypatch):
    load = {
        "_row_id": 2, "TYPE": "Import", "Status": "Ready to Dispatch",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(2, "En Route to Pickup")

    assert result["ok"] is True
    assert fake.audit_notes == []
    assert {"Status": "En Route to Pickup"} in fake.update_calls


def test_invalid_transition_without_driver_does_not_write_anything(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(1, "En Route to Pickup")

    assert result["ok"] is False
    assert fake.update_calls == []
    assert fake.audit_notes == []


def test_reaching_completed_sets_closeout_stage_to_pod_needed(monkeypatch):
    load = {
        "_row_id": 3, "TYPE": "Export", "Status": "At Delivery",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Barbours Cut",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(3, "Completed")

    assert result["ok"] is True
    assert fake.closeout_calls == ["POD Needed"]


def test_override_without_reason_is_rejected(monkeypatch):
    load = {
        "_row_id": 4, "TYPE": "Import", "Status": "Completed",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "POD Needed",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(4, "En Route to Pickup", override=True, override_reason="")

    assert result["ok"] is False
    assert fake.update_calls == []


def test_override_with_reason_allows_backward_transition(monkeypatch):
    load = {
        "_row_id": 5, "TYPE": "Import", "Status": "At Pickup",
        "Driver Name": "Sam", "Truck Assigned": "T2", "Port": "Bayport",
        "empty_return_location": "", "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    _wire(fake, monkeypatch)

    result = svc.apply_transition(5, "En Route to Pickup", override=True, override_reason="correction")

    assert result["ok"] is True
    assert any("override: correction" in u.get("Dispatcher Notes", "").lower() for u in fake.update_calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_transition_service.py -v`
Expected: FAIL (`_insert_assignment_audit` doesn't exist yet, signature mismatch).

- [ ] **Step 3: Rewrite `services/dispatch_transition_service.py`**

```python
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


def _insert_assignment_audit(load_id: int, current_status: str, notes: str) -> None:
    """Driver/truck assignment gets its own status_events row, distinct
    from the status-change row — old_status == new_status == the load's
    status at the time of assignment, so this reads clearly as an
    assignment event rather than a fake status transition."""
    execute(
        """
        insert into status_events (load_id, old_status, new_status, notes, created_by)
        values (:load_id, :status, :status, :notes, 'dispatcher')
        """,
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
    """
    if override and not override_reason.strip():
        return {"ok": False, "reason": "An override requires a reason.", "status": "", "closeout_stage": ""}

    df = _load_row(load_id)
    if df.empty:
        return {"ok": False, "reason": f"Load {load_id} not found.", "status": "", "closeout_stage": ""}

    row = df.iloc[0]
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
        return {"ok": False, "reason": reason, "status": current_status, "closeout_stage": str(row.get("closeout_stage", "Not Started"))}

    assignment_updates = {}
    if driver and driver.strip() and driver.strip() != existing_driver:
        assignment_updates["Driver Name"] = driver.strip()
    if truck and truck.strip() and truck.strip() != existing_truck:
        assignment_updates["Truck Assigned"] = truck.strip()

    if assignment_updates:
        _update_load(load_id, assignment_updates)
        parts = []
        if "Driver Name" in assignment_updates:
            parts.append(f"Driver assigned: {assignment_updates['Driver Name']}")
        if "Truck Assigned" in assignment_updates:
            parts.append(f"Truck assigned: {assignment_updates['Truck Assigned']}")
        _insert_assignment_audit(load_id, current_status, "; ".join(parts))

    status_updates: dict = {"Status": new_status}
    final_note = note.strip()
    if override:
        final_note = f"{final_note} [override: {override_reason.strip()}]".strip()
    if final_note:
        status_updates["Dispatcher Notes"] = final_note
    _update_load(load_id, status_updates)

    closeout_stage = str(row.get("closeout_stage", "Not Started") or "Not Started")
    if new_status == COMPLETION_STATUS and closeout_stage == "Not Started":
        closeout_stage = "POD Needed"
        _set_closeout_stage(load_id, closeout_stage)

    return {"ok": True, "reason": "", "status": new_status, "closeout_stage": closeout_stage}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_transition_service.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_transition_service.py tests/test_dispatch_transition_service.py
git commit -m "Record driver/truck assignment as a separate audit event from status transitions"
```

---

### Task 3: Rewrite `dispatch_legacy_status.py` — target the shared list

**Files:**
- Modify: `services/dispatch_legacy_status.py` (full rewrite)
- Modify: `tests/test_dispatch_legacy_status.py` (full rewrite)

**Interfaces:**
- Produces: `map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]` (unchanged signature, new target values).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_dispatch_legacy_status.py` with:
```python
from services.dispatch_legacy_status import map_legacy_status


def test_ready_to_dispatch_unchanged():
    assert map_legacy_status("Ready to Dispatch", "Import") == ("Ready to Dispatch", "Not Started")


def test_assigned_maps_to_ready_to_dispatch_not_a_stage():
    assert map_legacy_status("Assigned", "Import") == ("Ready to Dispatch", "Not Started")
    assert map_legacy_status("Driver Assigned", "Export") == ("Ready to Dispatch", "Not Started")


def test_en_route_to_pickup_same_for_every_move_type():
    for move_type in ["Import", "Export", "Local Import", "Local Export"]:
        assert map_legacy_status("En Route to Pickup", move_type) == ("En Route to Pickup", "Not Started")
    assert map_legacy_status("Dispatched", "Import") == ("En Route to Pickup", "Not Started")


def test_at_port_and_loaded_both_map_to_at_pickup():
    assert map_legacy_status("At Port", "Import") == ("At Pickup", "Not Started")
    assert map_legacy_status("Loaded", "Export") == ("At Pickup", "Not Started")
    assert map_legacy_status("Loaded / Picked Up", "Local Import") == ("At Pickup", "Not Started")


def test_delivered_maps_to_completed_with_pod_needed():
    assert map_legacy_status("Delivered", "Import") == ("Completed", "POD Needed")


def test_returning_empty_unchanged():
    assert map_legacy_status("Returning Empty", "Import") == ("Returning Empty", "POD Needed")


def test_pod_received_maps_to_completed_pod_received():
    assert map_legacy_status("POD Received", "Export") == ("Completed", "POD Received")


def test_ready_for_profittools_and_exported_map_to_completed_ready_for_profittools():
    assert map_legacy_status("Ready for ProfitTools", "Import") == ("Completed", "Ready for ProfitTools")
    assert map_legacy_status("Exported to ProfitTools", "Export") == ("Completed", "Ready for ProfitTools")


def test_invoiced_and_closed_map_to_completed_closed():
    assert map_legacy_status("Invoiced", "Export") == ("Completed", "Closed")
    assert map_legacy_status("Closed", "Local Export") == ("Completed", "Closed")


def test_cancelled_unchanged():
    assert map_legacy_status("Cancelled", "Import") == ("Cancelled", "Not Started")


def test_pre_dispatch_statuses_return_empty_operational_status():
    for legacy in ["New", "Hold/Need Info", "Booking Verified", "Port Verified", "PIN Received"]:
        assert map_legacy_status(legacy, "Import") == ("", "Not Started")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_legacy_status.py -v`
Expected: FAIL (old mapping returns move-type-specific values like "En Route to Port").

- [ ] **Step 3: Rewrite `services/dispatch_legacy_status.py`**

```python
from __future__ import annotations

_PRE_DISPATCH_STATUSES = {
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Awaiting Appointment",
    "New Email",
    "Needs Review",
    "Order Created",
}

# Legacy status -> (new shared canonical status, closeout_stage). No longer
# branches on move_type for the target value — canonical stages are shared
# across all move types now; move_type is only relevant for validating
# Returning Empty (handled by dispatch_stages, not here).
_DIRECT_MAP: dict[str, tuple[str, str]] = {
    "Ready to Dispatch": ("Ready to Dispatch", "Not Started"),
    "Assigned": ("Ready to Dispatch", "Not Started"),
    "Driver Assigned": ("Ready to Dispatch", "Not Started"),
    "Dispatched": ("En Route to Pickup", "Not Started"),
    "En Route to Pickup": ("En Route to Pickup", "Not Started"),
    "At Port": ("At Pickup", "Not Started"),
    "At Pickup": ("At Pickup", "Not Started"),
    "Loaded / Picked Up": ("At Pickup", "Not Started"),
    "Loaded": ("At Pickup", "Not Started"),
    "En Route To Delivery": ("En Route to Delivery", "Not Started"),
    "Delivered": ("Completed", "POD Needed"),
    "Returning Empty": ("Returning Empty", "POD Needed"),
    "POD Received": ("Completed", "POD Received"),
    "Ready for ProfitTools": ("Completed", "Ready for ProfitTools"),
    "Exported to ProfitTools": ("Completed", "Ready for ProfitTools"),
    "Invoiced": ("Completed", "Closed"),
    "Closed": ("Completed", "Closed"),
}


def map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]:
    """Map a legacy loads.status value to (new operational status, closeout_stage).

    move_type is accepted for API stability / future use but the target
    canonical value no longer depends on it — canonical stages are shared
    across move types in the new model. Returns ("", "Not Started") for
    statuses that predate operational dispatch.
    """
    status = (old_status or "").strip()

    if status == "Cancelled":
        return "Cancelled", "Not Started"

    if status in _PRE_DISPATCH_STATUSES:
        return "", "Not Started"

    if status in _DIRECT_MAP:
        return _DIRECT_MAP[status]

    return "", "Not Started"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_legacy_status.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_legacy_status.py tests/test_dispatch_legacy_status.py
git commit -m "Retarget legacy status mapping to the shared canonical stage list"
```

---

### Task 4: Rewrite `dispatch_board_view.py` — display labels + next action

**Files:**
- Modify: `services/dispatch_board_view.py` (full rewrite)
- Modify: `tests/test_dispatch_board_view.py` (full rewrite)

**Interfaces:**
- Produces: `get_display_label(move_type: str, canonical_status: str, *, via_empty_return: bool = False) -> str`, `get_board_columns() -> list[str]`, `is_active_dispatch_status(move_type: str, status: str) -> bool`, `get_next_action(move_type: str, status: str, *, has_driver: bool = False, empty_return_required: bool = False) -> tuple[str, str] | None`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_dispatch_board_view.py` with:
```python
from services.dispatch_board_view import (
    get_board_columns,
    get_display_label,
    get_next_action,
    is_active_dispatch_status,
)


def test_import_display_labels():
    assert get_display_label("Import", "En Route to Pickup") == "En Route to Port"
    assert get_display_label("Import", "At Pickup") == "At Port"
    assert get_display_label("Import", "At Delivery") == "At Delivery Warehouse"
    assert get_display_label("Import", "Completed") == "Completed"
    assert get_display_label("Import", "Completed", via_empty_return=True) == "Empty Returned"


def test_export_display_labels():
    assert get_display_label("Export", "En Route to Pickup") == "En Route to Pickup Warehouse"
    assert get_display_label("Export", "At Delivery") == "At Port"
    assert get_display_label("Export", "Completed") == "In-Gated"


def test_local_display_labels_use_origin_destination_wording():
    assert get_display_label("Local Import", "At Pickup") == "At Origin Warehouse"
    assert get_display_label("Local Export", "At Delivery") == "At Destination Warehouse"
    assert get_display_label("Local Import", "Completed") == "Completed"


def test_unmapped_status_falls_back_to_itself():
    assert get_display_label("Import", "Cancelled") == "Cancelled"


def test_get_board_columns_returns_shared_stages():
    columns = get_board_columns()
    assert columns[0] == "Ready to Dispatch"
    assert "Returning Empty" in columns
    assert columns[-1] == "Completed"


def test_is_active_dispatch_status_true_for_ready_and_later():
    assert is_active_dispatch_status("Import", "Ready to Dispatch") is True
    assert is_active_dispatch_status("Import", "At Pickup") is True


def test_is_active_dispatch_status_false_for_pre_dispatch_and_completed():
    assert is_active_dispatch_status("Import", "Booking Verified") is False
    assert is_active_dispatch_status("Import", "Completed") is False


def test_next_action_ready_to_dispatch_unassigned():
    assert get_next_action("Import", "Ready to Dispatch", has_driver=False) == ("Assign & Start", "En Route to Pickup")


def test_next_action_ready_to_dispatch_assigned():
    assert get_next_action("Import", "Ready to Dispatch", has_driver=True) == ("Start En Route", "En Route to Pickup")


def test_next_action_en_route_to_pickup():
    assert get_next_action("Import", "En Route to Pickup") == ("Mark Arrived", "At Pickup")


def test_next_action_at_pickup_import_vs_other():
    assert get_next_action("Import", "At Pickup") == ("Mark Container Picked Up", "En Route to Delivery")
    assert get_next_action("Export", "At Pickup") == ("Mark Loaded / Picked Up", "En Route to Delivery")


def test_next_action_at_delivery_import_with_and_without_empty_return():
    assert get_next_action("Import", "At Delivery", empty_return_required=True) == ("Start Empty Return", "Returning Empty")
    assert get_next_action("Import", "At Delivery", empty_return_required=False) == ("Complete Dispatch", "Completed")


def test_next_action_at_delivery_export_and_local():
    assert get_next_action("Export", "At Delivery") == ("Mark In-Gated", "Completed")
    assert get_next_action("Local Import", "At Delivery") == ("Mark Delivered", "Completed")


def test_next_action_returning_empty():
    assert get_next_action("Import", "Returning Empty") == ("Mark Empty Returned", "Completed")


def test_next_action_none_for_completed_and_cancelled():
    assert get_next_action("Import", "Completed") is None
    assert get_next_action("Import", "Cancelled") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_board_view.py -v`
Expected: FAIL (old `to_shared_stage`/`get_board_columns(filter)` shape doesn't match; `get_display_label`/`get_next_action` don't exist).

- [ ] **Step 3: Rewrite `services/dispatch_board_view.py`**

```python
from __future__ import annotations

from services.dispatch_stages import SHARED_STAGES, get_operational_stages
from services.workflow_constants import normalize_service_flow

_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "Import": {
        "En Route to Pickup": "En Route to Port",
        "At Pickup": "At Port",
        "En Route to Delivery": "En Route to Delivery Warehouse",
        "At Delivery": "At Delivery Warehouse",
    },
    "Export": {
        "En Route to Pickup": "En Route to Pickup Warehouse",
        "At Pickup": "At Pickup Warehouse",
        "En Route to Delivery": "En Route to Port",
        "At Delivery": "At Port",
        "Completed": "In-Gated",
    },
    "Local Import": {
        "En Route to Pickup": "En Route to Origin Warehouse",
        "At Pickup": "At Origin Warehouse",
        "En Route to Delivery": "En Route to Destination Warehouse",
        "At Delivery": "At Destination Warehouse",
    },
}
_DISPLAY_LABELS["Local Export"] = _DISPLAY_LABELS["Local Import"]


def get_display_label(move_type: str, canonical_status: str, *, via_empty_return: bool = False) -> str:
    """Contextual, move-type-specific label for a canonical status. Purely
    cosmetic — never stored. Falls back to the canonical status itself if
    this move type has no override for it."""
    normalized = normalize_service_flow(move_type, default="Local Import")
    if normalized == "Import" and canonical_status == "Completed" and via_empty_return:
        return "Empty Returned"
    return _DISPLAY_LABELS.get(normalized, {}).get(canonical_status, canonical_status)


def get_board_columns() -> list[str]:
    """Canonical stages are shared across move types now, so there's one
    status-filter option list. A move type that doesn't use a given stage
    (e.g. Export + Returning Empty) simply never has rows in it."""
    return list(SHARED_STAGES)


def is_active_dispatch_status(move_type: str, status: str) -> bool:
    stages = get_operational_stages(move_type)
    return status in stages and status != "Completed"


def get_next_action(
    move_type: str,
    status: str,
    *,
    has_driver: bool = False,
    empty_return_required: bool = False,
) -> tuple[str, str] | None:
    """Return (button_label, target_canonical_status) for the next valid
    operational action from this status, or None if there isn't one
    (Completed / Cancelled — no forward action)."""
    normalized = normalize_service_flow(move_type, default="Local Import")

    if status == "Ready to Dispatch":
        label = "Start En Route" if has_driver else "Assign & Start"
        return label, "En Route to Pickup"
    if status == "En Route to Pickup":
        return "Mark Arrived", "At Pickup"
    if status == "At Pickup":
        label = "Mark Container Picked Up" if normalized == "Import" else "Mark Loaded / Picked Up"
        return label, "En Route to Delivery"
    if status == "En Route to Delivery":
        return "Mark Arrived", "At Delivery"
    if status == "At Delivery":
        if normalized == "Import" and empty_return_required:
            return "Start Empty Return", "Returning Empty"
        if normalized == "Import":
            return "Complete Dispatch", "Completed"
        if normalized == "Export":
            return "Mark In-Gated", "Completed"
        return "Mark Delivered", "Completed"
    if status == "Returning Empty":
        return "Mark Empty Returned", "Completed"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_board_view.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_board_view.py tests/test_dispatch_board_view.py
git commit -m "Add display-label and next-action lookups for the shared canonical stage model"
```

---

### Task 5: Row-based Dispatch Board UI

**Files:**
- Modify: `pages_app/dispatch_board.py` (imports; new `_render_dispatch_row` function; new `_render_row_next_action` function; replace `_render_dispatch_action_card`/`_render_dispatch_action_group_card` call sites and the column-loop body of `render_dispatch_board_focused` with a row loop)

**Interfaces:**
- Consumes: `services.dispatch_board_view.{get_board_columns, get_display_label, get_next_action, is_active_dispatch_status}`, `services.dispatch_transition_service.apply_transition`, `services.load_grouping_service.group_loads_by_booking` (unchanged, reused).
- `_render_dispatch_action_card`/`_render_dispatch_action_group_card` (column-based cards) are removed — replaced entirely by row rendering. Confirm no other file imports them (`grep -rn "_render_dispatch_action_card\|_render_dispatch_action_group_card" pages_app/` before deleting — expected: only `dispatch_board.py` itself).

- [ ] **Step 1: Update imports**

Replace:
```python
from services.dispatch_board_view import get_board_columns, is_active_dispatch_status, to_shared_stage
```
with:
```python
from services.dispatch_board_view import (
    get_board_columns,
    get_display_label,
    get_next_action,
    is_active_dispatch_status,
)
```

- [ ] **Step 2: Replace `_render_dispatch_action_card` and `_render_dispatch_action_group_card` with row rendering**

Remove both functions entirely (confirm via grep first per Interfaces note above) and add in their place:

```python
def _risk_level(row) -> str:
    """"" (healthy), "risk" (approaching/unassigned), or "late" (severe)."""
    status = str(row.get("Status", "") or "")
    if status in ("Completed", "Cancelled"):
        return ""
    delivery_date = pd.to_datetime(row.get("Delivery Need Date", ""), errors="coerce")
    today = pd.Timestamp(pd.Timestamp.now().date())
    if pd.notna(delivery_date) and delivery_date.normalize() < today:
        return "late"
    exceptions = _safe_str(row.get("Exceptions", ""))
    if exceptions:
        return "risk"
    if status == "Ready to Dispatch" and not str(row.get("Driver Name", "") or "").strip():
        return "risk"
    return ""


_RISK_BADGE = {
    "late": ('<span style="background:#fee2e2;color:#991b1b;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">Late</span>'),
    "risk": ('<span style="background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">Risk</span>'),
    "": "",
}


def _render_row_next_action(row, load_id: int, move_type: str, canonical_status: str, empty_return_required: bool, refresh_callback) -> None:
    has_driver = bool(str(row.get("Driver Name", "") or "").strip())
    action = get_next_action(move_type, canonical_status, has_driver=has_driver, empty_return_required=empty_return_required)

    if action is None:
        st.caption("—")
        return

    label, target_status = action
    needs_assignment = canonical_status == "Ready to Dispatch" and not has_driver

    if needs_assignment:
        assign_key = f"assign_open_{load_id}"
        if st.session_state.get(assign_key):
            driver_input = st.text_input("Driver", key=f"assign_driver_{load_id}", label_visibility="collapsed", placeholder="Driver name")
            truck_input = st.text_input("Truck", key=f"assign_truck_{load_id}", label_visibility="collapsed", placeholder="Truck")
            if st.button("Confirm", key=f"assign_confirm_{load_id}", use_container_width=True):
                if not driver_input.strip():
                    st.error("Driver is required.")
                else:
                    result = apply_transition(load_id, target_status, driver=driver_input.strip(), truck=truck_input.strip())
                    if not result["ok"]:
                        st.error(result["reason"])
                    else:
                        st.session_state.pop(assign_key, None)
                        _run_refresh(refresh_callback)
                        st.rerun()
        else:
            if st.button(label, key=f"next_action_{load_id}", use_container_width=True):
                st.session_state[assign_key] = True
                st.rerun()
        return

    if st.button(label, key=f"next_action_{load_id}", use_container_width=True):
        result = apply_transition(load_id, target_status)
        if not result["ok"]:
            st.error(result["reason"])
        else:
            _run_refresh(refresh_callback)
            st.rerun()


def _render_dispatch_row(row, refresh_callback) -> None:
    load_id = _int_or_none(row.get("_row_id")) or 0
    move_type = _clean_display_value(row.get("Dispatch Move Type", ""), _normalize_load_type(row))
    canonical_status = _clean_display_value(row.get("Status", ""), "New")
    empty_return_required = bool(str(row.get("empty_return_location", "") or "").strip())
    display_status = get_display_label(move_type, canonical_status)

    booking = _clean_display_value(row.get("Booking Number", ""), "-")
    container = _clean_display_value(row.get("Container Number", ""), "-")
    customer = _clean_display_value(row.get("Customer", ""), "-")
    origin = _clean_display_value(row.get("Port", "") or row.get("Warehouse", ""), "-")
    destination = _clean_display_value(row.get("Warehouse", "") or row.get("Address", ""), "-")
    need_date = _clean_display_value(row.get("Delivery Need Date", ""), "-")
    lfd = _clean_display_value(row.get("LFD", ""), "-")
    driver = _clean_display_value(row.get("Driver Name", ""), "Unassigned")
    truck = _clean_display_value(row.get("Truck Assigned", ""), "-")
    chassis = _clean_display_value(row.get("Chassis", ""), "-")
    eta = _clean_display_value(row.get("eta", ""), "-")
    exceptions = [item.strip() for item in _safe_str(row.get("Exceptions", "")).split(",") if item.strip()]
    risk = _risk_level(row)

    cols = st.columns([0.6, 1.4, 0.8, 1.1, 1.3, 1.3, 1.0, 1.0, 1.0, 0.9, 1.1, 1.3, 0.7])

    with cols[0]:
        badge = _RISK_BADGE.get(risk, "")
        if badge:
            st.markdown(badge, unsafe_allow_html=True)
    with cols[1]:
        st.markdown(f"**{escape(booking)}**")
        st.caption(f"{container} · {customer}")
    with cols[2]:
        st.caption(move_type)
    with cols[3]:
        st.markdown(render_status_badge(display_status) or escape(display_status), unsafe_allow_html=True)
    with cols[4]:
        st.caption(origin)
    with cols[5]:
        st.caption(destination)
    with cols[6]:
        st.caption(need_date)
        if lfd != "-":
            st.caption(f"LFD {lfd}")
    with cols[7]:
        st.caption(driver)
    with cols[8]:
        st.caption(f"{truck} / {chassis}")
    with cols[9]:
        st.caption(eta)
    with cols[10]:
        if exceptions:
            st.caption(", ".join(exceptions[:2]) + (f" +{len(exceptions) - 2}" if len(exceptions) > 2 else ""))
    with cols[11]:
        _render_row_next_action(row, load_id, move_type, canonical_status, empty_return_required, refresh_callback)
    with cols[12]:
        if st.button("Open", key=f"open_row_{load_id}", use_container_width=True):
            st.session_state["dispatch_board_selected_row_id"] = load_id
            st.rerun()

    with st.expander(f"Details — {booking}", expanded=False):
        detail_cols = st.columns(3)
        detail_cols[0].write(f"**Address:** {_clean_display_value(row.get('Address', ''), '-')}")
        detail_cols[0].write(f"**Terminal:** {_clean_display_value(row.get('terminal', ''), '-')}")
        detail_cols[1].write(f"**Pickup Appt:** {_clean_display_value(row.get('pickup_appointment', ''), '-')}")
        detail_cols[1].write(f"**Delivery Appt:** {_clean_display_value(row.get('delivery_appointment', ''), '-')}")
        detail_cols[2].write(f"**Empty Return:** {_clean_display_value(row.get('empty_return_location', ''), '-')}")
        detail_cols[2].write(f"**Notes:** {_clean_display_value(row.get('Dispatcher Notes', ''), '-')}")
        if exceptions:
            st.warning("Exceptions: " + ", ".join(exceptions))

    st.divider()


def _render_dispatch_row_group(group_row, refresh_callback) -> None:
    """A collapsed multi-container booking: show the summary row (using
    the first container's data) plus a picker to open one specific
    container, reusing the same pattern proven for the column board."""
    row_ids = list(group_row.get("_grouped_row_ids", []))
    load_id = _int_or_none(group_row.get("_row_id")) or 0
    containers_label = group_row.get("Containers") or f"{len(row_ids)} containers"

    _render_dispatch_row(group_row, refresh_callback)
    st.caption(f"📦 {containers_label} in this booking")
    picker_key = f"dispatch_row_group_picker_{load_id}"
    if st.session_state.get(f"{picker_key}_open"):
        for row_id in row_ids:
            if st.button(f"Open container (load {row_id})", key=f"{picker_key}_{row_id}", use_container_width=True):
                st.session_state["dispatch_board_selected_row_id"] = row_id
                st.session_state.pop(f"{picker_key}_open", None)
                st.rerun()
    else:
        if st.button("Show all containers in this booking", key=f"{picker_key}_toggle", use_container_width=True):
            st.session_state[f"{picker_key}_open"] = True
            st.rerun()
```

Note: `_render_dispatch_row_group` renders the SAME row for the summary (using the first container's data, same as the old group card did) — this is a reasonable simplification for this batch; a future batch could show aggregate info (e.g. "4 containers, 2 en route") instead.

- [ ] **Step 3: Replace the board-columns loop with a row list**

Find the metrics-and-column-loop body of `render_dispatch_board_focused` (the block starting at `board_stage_column = "Board Stage" if selected_flow == "All" else "Status"` through the end of the `for idx, stage_name in enumerate(columns):` loop). Re-read the file to get exact current line numbers before editing (line numbers have shifted since the last read this session).

Replace the status-filter control and the whole `if scope_df.empty: ... else: columns = get_board_columns(...) ...` block with:

```python
    status_filter = st.selectbox(
        "Status Filter",
        ["All Active"] + get_board_columns(),
        key="dispatch_board_status_filter",
    )
    if status_filter != "All Active":
        scope_df = scope_df[scope_df["Status"].eq(status_filter)].copy()

    if scope_df.empty:
        st.info("No active dispatch loads match the current Dispatch Board filters.")
    else:
        severity_rank = {"late": 0, "risk": 1, "": 2}
        scope_df["_risk"] = scope_df.apply(_risk_level, axis=1)
        scope_df["_risk_sort"] = scope_df["_risk"].map(severity_rank).fillna(2)
        sorted_df = scope_df.sort_values(
            ["_risk_sort", "Exception Count", "Delivery Date Parsed", "LFD Parsed", "_row_id"],
            ascending=[True, False, True, True, True],
            na_position="last",
        )
        grouped_df = group_loads_by_booking(sorted_df, require_same_status=True)
        header_cols = st.columns([0.6, 1.4, 0.8, 1.1, 1.3, 1.3, 1.0, 1.0, 1.0, 0.9, 1.1, 1.3, 0.7])
        headers = ["Risk", "Load", "Type", "Status", "Origin", "Destination", "Appt / LFD", "Driver", "Truck/Chassis", "ETA", "Exceptions", "Next Action", ""]
        for col, label in zip(header_cols, headers):
            col.caption(f"**{label}**" if label else "")
        for _, row in grouped_df.iterrows():
            row_ids = row.get("_grouped_row_ids", [])
            if len(row_ids) > 1:
                _render_dispatch_row_group(row, refresh_callback)
            else:
                _render_dispatch_row(row, refresh_callback)
```

This removes the earlier per-column `board_cols`/`get_board_columns(selected_flow)` loop (columns are now just filter options, not layout buckets) and the `Board Stage`/`Board Stage Sort` columns computed in the section above it — those become unnecessary since every row now displays its own `Status` directly (via `get_display_label`) rather than being bucketed into a shared/per-type column set. Remove the now-unused `board_df["Board Stage"]` assignment and the `board_stage_column`/`to_shared_stage` usage earlier in the function (the `to_shared_stage` import from Task 4's rewrite no longer exists — `dispatch_board_view.py` no longer exports it, so this must be removed, not just left unused, or the import will fail).

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
```
Expected: exit 0. If it fails on a leftover `to_shared_stage`/`Board Stage` reference, remove it — Task 4 deleted that function.

Check for orphaned imports/names (same technique used earlier this session):
```powershell
python -c "import ast,sys; tree = ast.parse(open('pages_app/dispatch_board.py', encoding='utf-8').read()); print('parsed OK')"
```

Visually, with the running app (restart it — this is a big enough change that a hot-reload is not to be trusted, per this session's precedent):
  - Dispatch Board shows one row per load, not narrow columns.
  - A `Ready to Dispatch` load with no driver shows "Assign & Start"; clicking it reveals driver/truck inputs inline; confirming moves it to `En Route to Pickup` (displayed as "En Route to Port" for an Import load).
  - A `Ready to Dispatch` load that already has a driver shows "Start En Route" and moves immediately on click.
  - The RICGX1235800 multi-container booking (once advanced past "New") still shows as one row with a "Show all containers" picker.
  - No literal `<div style=` text anywhere (only single-line `unsafe_allow_html=True` markdown remains).

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Replace Dispatch Board column layout with row-based board, contextual display labels, and driver-assignment-as-audit-event"
```

---

### Task 6: Full verification

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all tests pass (prior count minus the rewritten dispatch test files' old counts, plus their new counts — verify the total makes sense rather than assuming an exact number, since several test files were fully replaced in this plan, not just added to).

- [ ] **Step 3: Restart the running app and do a full manual pass**

Kill and restart the Streamlit server (per this session's established need after large multi-file changes), then walk through: a Ready-to-Dispatch Import load with no driver (Assign & Start), an Export load through to In-Gated/Completed, a Local load through to Delivered/Completed, and the multi-container booking's row + picker.
