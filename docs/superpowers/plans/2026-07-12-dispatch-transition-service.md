# Dispatch Transition Service Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the centralized transition/workflow backend from `docs/superpowers/specs/2026-07-12-dispatch-board-workflow-spec.md` — no UI changes in this plan. This becomes the *only* code path allowed to change a load's operational status going forward.

**Architecture:** A new pure module (`dispatch_stages.py`) holds the per-move-type stage tables and validation logic with zero DB/Streamlit imports, fully unit-testable. A second module (`dispatch_transition_service.py`) wraps it with the actual DB write, reusing `DispatchDatabaseClient.update_row_fields()` (which already inserts `status_events` audit rows on status change — not duplicated). A third module (`dispatch_exception_service.py`) handles exception create/resolve against a new small table. Legacy status mapping is a pure function, tested against the exact `(status, type)` combinations confirmed live in the database this session.

**Tech Stack:** Python, pandas (for type hints only where needed), SQLAlchemy via existing `db_client.execute`/`read_df`. No new dependencies.

## Global Constraints

- `loads.status` = operational only, going forward. `loads.closeout_stage` (new column) = billing/closeout only. Never conflate the two.
- Reuse `DispatchDatabaseClient.update_row_fields()` for the actual `loads` write and its existing `status_events` audit-insert-on-status-change — do not build a second audit mechanism.
- Do not change `_editable_db_columns()`'s allowlist mechanism.
- Do not touch `pages_app/dispatch_board.py` in this plan — UI wiring is Phase 4, a separate plan.
- Move-type values are always the *normalized* form (`Import`, `Export`, `Local Import`, `Local Export`) — always pass raw `type`/`TYPE` values through `normalize_service_flow()` first, never compare against raw DB values directly.
- All new DB objects (the `closeout_stage` column, the `load_exceptions` table) are additive only. Present Impact/Migration/Rollback and get explicit approval before running any migration — same process used earlier this session.

---

### Task 1: Pure stage tables and transition validation

**Files:**
- Create: `services/dispatch_stages.py`
- Test: `tests/test_dispatch_stages.py`

**Interfaces:**
- Produces: `OPERATIONAL_STAGES: dict[str, list[str]]`, `CLOSEOUT_STAGES: list[str]`, `COMPLETION_STATUS = "Dispatch Complete"`, `CANCELLED_STATUS = "Cancelled"`, `get_operational_stages(move_type: str) -> list[str]`, `validate_transition(move_type: str, current_status: str, new_status: str, *, has_driver: bool = False, has_truck: bool = False, has_origin: bool = False, empty_return_required: bool = False, override: bool = False) -> tuple[bool, str]`. Task 2 imports all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_stages.py`:
```python
from services.dispatch_stages import (
    CANCELLED_STATUS,
    COMPLETION_STATUS,
    get_operational_stages,
    validate_transition,
)


def test_import_stage_order():
    stages = get_operational_stages("Import")
    assert stages == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Port",
        "At Port",
        "Container Picked Up",
        "En Route to Delivery Warehouse",
        "At Delivery Warehouse",
        "Delivered",
        "Returning Empty",
        "Empty Returned",
        "Dispatch Complete",
    ]


def test_export_stage_order():
    stages = get_operational_stages("Export")
    assert stages == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Pickup Warehouse",
        "At Pickup Warehouse",
        "Container Loaded",
        "En Route to Port",
        "At Port",
        "In-Gated",
        "Dispatch Complete",
    ]


def test_local_import_and_local_export_share_stage_shape():
    assert get_operational_stages("Local Import") == get_operational_stages("Local Export")
    assert get_operational_stages("Local Import") == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ]


def test_unknown_move_type_falls_back_to_local_import_shape():
    assert get_operational_stages("Other") == get_operational_stages("Local Import")


def test_cannot_assign_without_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=False, has_truck=True)
    assert ok is False
    assert "driver" in reason.lower()


def test_cannot_assign_without_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=True, has_truck=False)
    assert ok is False


def test_can_assign_with_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=True, has_truck=True)
    assert ok is True
    assert reason == ""


def test_cannot_go_en_route_without_origin():
    ok, reason = validate_transition(
        "Import", "Driver Assigned", "En Route to Port",
        has_driver=True, has_truck=True, has_origin=False,
    )
    assert ok is False
    assert "origin" in reason.lower()


def test_cannot_reach_at_pickup_before_assigned():
    ok, reason = validate_transition("Export", "Ready to Dispatch", "At Pickup Warehouse", has_driver=False, has_truck=False)
    assert ok is False


def test_import_cannot_return_empty_before_delivered():
    ok, reason = validate_transition(
        "Import", "At Delivery Warehouse", "Returning Empty",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False
    assert "delivered" in reason.lower()


def test_import_can_return_empty_after_delivered():
    ok, reason = validate_transition(
        "Import", "Delivered", "Returning Empty",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is True


def test_export_cannot_in_gate_before_at_port():
    ok, reason = validate_transition(
        "Export", "En Route to Port", "In-Gated",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False
    assert "port" in reason.lower()


def test_export_can_in_gate_after_at_port():
    ok, reason = validate_transition(
        "Export", "At Port", "In-Gated",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is True


def test_import_cannot_complete_before_delivered():
    ok, reason = validate_transition(
        "Import", "Container Picked Up", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False


def test_import_complete_requires_empty_returned_when_required():
    ok, reason = validate_transition(
        "Import", "Delivered", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True, empty_return_required=True,
    )
    assert ok is False
    assert "empty returned" in reason.lower()


def test_import_complete_ok_from_delivered_when_no_empty_return_required():
    ok, reason = validate_transition(
        "Import", "Delivered", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True, empty_return_required=False,
    )
    assert ok is True


def test_completed_load_blocks_further_operational_transitions():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Port", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_completed_load_allows_transition_with_override():
    ok, reason = validate_transition(
        "Import", COMPLETION_STATUS, "En Route to Port",
        has_driver=True, has_truck=True, has_origin=True, override=True,
    )
    assert ok is True


def test_cancel_allowed_from_active_status():
    ok, reason = validate_transition("Import", "En Route to Port", CANCELLED_STATUS)
    assert ok is True


def test_cannot_cancel_a_completed_load():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, CANCELLED_STATUS)
    assert ok is False


def test_backward_transition_blocked_without_override():
    ok, reason = validate_transition(
        "Import", "At Port", "Driver Assigned",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False


def test_backward_transition_allowed_with_override():
    ok, reason = validate_transition(
        "Import", "At Port", "Driver Assigned",
        has_driver=True, has_truck=True, has_origin=True, override=True,
    )
    assert ok is True


def test_unknown_new_status_rejected():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Not A Real Status")
    assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_stages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.dispatch_stages'`

- [ ] **Step 3: Implement `services/dispatch_stages.py`**

```python
from __future__ import annotations

from services.workflow_constants import normalize_service_flow

COMPLETION_STATUS = "Dispatch Complete"
CANCELLED_STATUS = "Cancelled"

OPERATIONAL_STAGES: dict[str, list[str]] = {
    "Import": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Port",
        "At Port",
        "Container Picked Up",
        "En Route to Delivery Warehouse",
        "At Delivery Warehouse",
        "Delivered",
        "Returning Empty",
        "Empty Returned",
        "Dispatch Complete",
    ],
    "Export": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Pickup Warehouse",
        "At Pickup Warehouse",
        "Container Loaded",
        "En Route to Port",
        "At Port",
        "In-Gated",
        "Dispatch Complete",
    ],
    "Local Import": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ],
    "Local Export": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ],
}

CLOSEOUT_STAGES = [
    "Not Started",
    "POD Needed",
    "POD Received",
    "Documents Review",
    "Accessorial Review",
    "Rate Verification",
    "Ready to Invoice",
    "Invoice Sent",
    "Ready for ProfitTools",
    "Closed",
]

# Status a load must have reached (or passed) before it can become
# Dispatch Complete, per move type. Import overrides this to "Empty
# Returned" when empty_return_required=True (see validate_transition).
_COMPLETION_MILESTONE = {
    "Import": "Delivered",
    "Export": "In-Gated",
    "Local Import": "Delivered",
    "Local Export": "Delivered",
}

_ASSIGN_GATED_STATUSES = {"Driver Assigned"}
_AT_LOCATION_STATUSES = {"At Port", "At Pickup Warehouse", "At Origin Warehouse"}


def get_operational_stages(move_type: str) -> list[str]:
    normalized = normalize_service_flow(move_type, default="Local Import")
    return OPERATIONAL_STAGES.get(normalized, OPERATIONAL_STAGES["Local Import"])


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

    override=True bypasses the completed-load lock and the forward-skip /
    backward-move guard, but never bypasses the hard business rules
    (assignment required, origin required, Returning Empty requires
    Delivered, In-Gated requires At Port, Dispatch Complete requires
    reaching the move type's completion milestone) — those always apply.
    """
    stages = get_operational_stages(move_type)

    if new_status == CANCELLED_STATUS:
        if current_status == COMPLETION_STATUS:
            return False, "Cannot cancel a load that is already Dispatch Complete."
        return True, ""

    if new_status not in stages:
        return False, f"'{new_status}' is not a valid operational status for {move_type}."

    if current_status in (COMPLETION_STATUS, CANCELLED_STATUS) and not override:
        return False, f"Load is {current_status}; further operational status changes require an override."

    current_index = _stage_index(stages, current_status)
    new_index = stages.index(new_status)
    assign_index = _stage_index(stages, "Driver Assigned")

    if new_status in _ASSIGN_GATED_STATUSES and not (has_driver and has_truck):
        return False, "Driver and truck must be assigned before this status."

    if new_status.startswith("En Route") and not has_origin:
        return False, f"Cannot move to '{new_status}' without a valid origin."

    if new_status in _AT_LOCATION_STATUSES and assign_index is not None:
        if current_index is None or current_index < assign_index:
            return False, f"Cannot move to '{new_status}' before the load has been assigned."

    if move_type == "Import" and new_status == "Returning Empty":
        delivered_index = stages.index("Delivered")
        if current_index is None or current_index < delivered_index:
            return False, "Cannot start empty return before the load has been Delivered."

    if move_type == "Export" and new_status == "In-Gated":
        at_port_index = stages.index("At Port")
        if current_index is None or current_index < at_port_index:
            return False, "Cannot mark In-Gated before the load has reached the port."

    if new_status == COMPLETION_STATUS:
        milestone = _COMPLETION_MILESTONE.get(move_type, "Delivered")
        if move_type == "Import" and empty_return_required:
            milestone = "Empty Returned"
        milestone_index = stages.index(milestone)
        if current_index is None or current_index < milestone_index:
            return False, f"Cannot mark Dispatch Complete before reaching '{milestone}'."

    if not override and current_index is not None:
        if new_index > current_index + 1:
            return False, f"Cannot skip from '{current_status}' directly to '{new_status}' without an override."
        if new_index < current_index:
            return False, f"Cannot move backward from '{current_status}' to '{new_status}' without an override."

    return True, ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_stages.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_stages.py tests/test_dispatch_stages.py
git commit -m "Add pure dispatch stage tables and transition validation"
```

---

### Task 2: Migration — `closeout_stage` column

**Files:**
- Create: `database/dispatch_closeout_migration.sql`

**Interfaces:** none (schema only).

- [ ] **Step 1: Write and present the migration**

```sql
-- Calitrans TMS Dispatch Closeout migration
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Splits billing/closeout progress out of loads.status (which becomes
-- operational-only) into its own independent column, so a load can be
-- Dispatch Complete while closeout is still POD Needed.

alter table loads add column if not exists closeout_stage text not null default 'Not Started';

create index if not exists idx_loads_closeout_stage on loads(closeout_stage);

-- ============================================================
-- ROLLBACK
-- ============================================================
-- drop index if exists idx_loads_closeout_stage;
-- alter table loads drop column if exists closeout_stage;
```

This step is **present only** — do not run it until the user explicitly
approves, per this session's established pattern (Impact: purely additive,
one nullable-with-default column; Rollback: included above).

- [ ] **Step 2: Commit the migration file**

```bash
git add database/dispatch_closeout_migration.sql
git commit -m "Add closeout_stage migration (not yet applied)"
```

---

### Task 3: Transition service (DB-writing layer)

**Files:**
- Create: `services/dispatch_transition_service.py`
- Test: `tests/test_dispatch_transition_service.py`

**Interfaces:**
- Consumes: `services.dispatch_stages.validate_transition`, `db_client.DispatchDatabaseClient`, `db_client.read_df`.
- Produces: `apply_transition(load_id: int, new_status: str, *, note: str = "", override: bool = False, override_reason: str = "") -> dict` — returns `{"ok": bool, "reason": str, "status": str, "closeout_stage": str}`.
- **Depends on Task 2's migration being applied** before `apply_transition` can write `closeout_stage` — the test suite in this task uses a fake/stub DB layer (see below) so it does not require the live migration to be run to pass.

- [ ] **Step 1: Write the failing tests using a stub DB layer**

Create `tests/test_dispatch_transition_service.py`:
```python
import pandas as pd
import pytest

from services import dispatch_transition_service as svc


class _FakeDb:
    """In-memory stand-in for DispatchDatabaseClient + db_client.read_df/execute,
    scoped to exactly what apply_transition needs."""

    def __init__(self, load: dict):
        self.load = dict(load)
        self.update_calls = []
        self.executed_sql = []

    def read_load(self, load_id: int) -> pd.DataFrame:
        return pd.DataFrame([self.load])

    def update_row_fields(self, load_id: int, updates: dict) -> None:
        self.update_calls.append(dict(updates))
        self.load.update(updates)

    def execute_closeout(self, load_id: int, closeout_stage: str) -> None:
        self.executed_sql.append(closeout_stage)
        self.load["closeout_stage"] = closeout_stage


@pytest.fixture
def import_load():
    return {
        "_row_id": 1,
        "TYPE": "Import",
        "Status": "Ready to Dispatch",
        "Driver Name": "Alex",
        "Truck Assigned": "T1",
        "Port": "Bayport",
        "closeout_stage": "Not Started",
    }


def test_valid_transition_updates_status_and_calls_update_row_fields(import_load, monkeypatch):
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", note="dispatcher confirmed")

    assert result["ok"] is True
    assert fake.update_calls[0]["Status"] == "Driver Assigned"


def test_invalid_transition_does_not_call_update_row_fields(import_load, monkeypatch):
    import_load["Driver Name"] = ""
    import_load["Truck Assigned"] = ""
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned")

    assert result["ok"] is False
    assert fake.update_calls == []


def test_reaching_completion_milestone_sets_closeout_stage_to_pod_needed(monkeypatch):
    load = {
        "_row_id": 2,
        "TYPE": "Export",
        "Status": "At Port",
        "Driver Name": "Sam",
        "Truck Assigned": "T2",
        "Port": "Barbours Cut",
        "closeout_stage": "Not Started",
    }
    fake = _FakeDb(load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(2, "In-Gated")
    assert result["ok"] is True

    result2 = svc.apply_transition(2, "Dispatch Complete")
    assert result2["ok"] is True
    assert fake.executed_sql == ["POD Needed"]


def test_closeout_stage_not_overwritten_if_already_past_not_started(monkeypatch):
    load = {
        "_row_id": 3,
        "TYPE": "Local Import",
        "Status": "At Destination Warehouse",
        "Driver Name": "Sam",
        "Truck Assigned": "T2",
        "Warehouse": "Origin WH",
        "closeout_stage": "POD Received",
    }
    fake = _FakeDb(load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    fake.load["Status"] = "Delivered"
    result = svc.apply_transition(3, "Dispatch Complete")
    assert result["ok"] is True
    assert fake.executed_sql == []


def test_override_allows_backward_transition_with_reason(import_load, monkeypatch):
    import_load["Status"] = "At Port"
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", override=True, override_reason="dispatcher correction")

    assert result["ok"] is True
    assert "override: dispatcher correction" in fake.update_calls[0]["Dispatcher Notes"].lower()


def test_override_true_without_reason_is_rejected(import_load, monkeypatch):
    import_load["Status"] = "At Port"
    fake = _FakeDb(import_load)
    monkeypatch.setattr(svc, "_load_row", fake.read_load)
    monkeypatch.setattr(svc, "_update_load", fake.update_row_fields)
    monkeypatch.setattr(svc, "_set_closeout_stage", fake.execute_closeout)

    result = svc.apply_transition(1, "Driver Assigned", override=True, override_reason="")

    assert result["ok"] is False
    assert fake.update_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_transition_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.dispatch_transition_service'`

- [ ] **Step 3: Implement `services/dispatch_transition_service.py`**

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_transition_service.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_transition_service.py tests/test_dispatch_transition_service.py
git commit -m "Add dispatch_transition_service.apply_transition() with tests"
```

---

### Task 4: Legacy status compatibility mapping

**Files:**
- Create: `services/dispatch_legacy_status.py`
- Test: `tests/test_dispatch_legacy_status.py`

**Interfaces:**
- Produces: `map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]` returning `(new_operational_status, closeout_stage)`. Returns `("", "Not Started")` for pre-dispatch/intake statuses that don't belong on `loads.status` in the new model (caller decides what to do with those — this plan does not change what happens to pre-dispatch loads).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_legacy_status.py`:
```python
from services.dispatch_legacy_status import map_legacy_status


def test_ready_to_dispatch_maps_unchanged():
    assert map_legacy_status("Ready to Dispatch", "Import") == ("Ready to Dispatch", "Not Started")


def test_assigned_maps_to_driver_assigned():
    assert map_legacy_status("Assigned", "Import") == ("Driver Assigned", "Not Started")
    assert map_legacy_status("Driver Assigned", "Local Import") == ("Driver Assigned", "Not Started")


def test_en_route_to_pickup_is_move_type_specific():
    assert map_legacy_status("En Route to Pickup", "Import") == ("En Route to Port", "Not Started")
    assert map_legacy_status("En Route to Pickup", "Export") == ("En Route to Pickup Warehouse", "Not Started")
    assert map_legacy_status("En Route to Pickup", "Local Import") == ("En Route to Origin Warehouse", "Not Started")


def test_delivered_sets_closeout_pod_needed():
    assert map_legacy_status("Delivered", "Import") == ("Delivered", "POD Needed")


def test_pod_received_maps_to_dispatch_complete_and_pod_received():
    assert map_legacy_status("POD Received", "Export") == ("Dispatch Complete", "POD Received")


def test_ready_for_profittools_maps_to_dispatch_complete_and_closeout():
    assert map_legacy_status("Ready for ProfitTools", "Import") == ("Dispatch Complete", "Ready for ProfitTools")


def test_invoiced_and_closed_map_to_closed_closeout():
    assert map_legacy_status("Invoiced", "Export") == ("Dispatch Complete", "Closed")
    assert map_legacy_status("Closed", "Local Export") == ("Dispatch Complete", "Closed")


def test_cancelled_is_unchanged():
    assert map_legacy_status("Cancelled", "Import") == ("Cancelled", "Not Started")


def test_pre_dispatch_statuses_return_empty_operational_status():
    for legacy in ["New", "Hold/Need Info", "Booking Verified", "Port Verified", "PIN Received"]:
        new_status, closeout = map_legacy_status(legacy, "Import")
        assert new_status == ""
        assert closeout == "Not Started"


def test_dispatched_maps_to_first_en_route_stage_per_move_type():
    assert map_legacy_status("Dispatched", "Import") == ("En Route to Port", "Not Started")
    assert map_legacy_status("Dispatched", "Export") == ("En Route to Pickup Warehouse", "Not Started")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_legacy_status.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/dispatch_legacy_status.py`**

```python
from __future__ import annotations

from services.workflow_constants import normalize_service_flow

_MOVE_TYPE_ENROUTE_PICKUP_LEG = {
    "Import": "En Route to Port",
    "Export": "En Route to Pickup Warehouse",
    "Local Import": "En Route to Origin Warehouse",
    "Local Export": "En Route to Origin Warehouse",
}

_MOVE_TYPE_AT_PICKUP_LEG = {
    "Import": "At Port",
    "Export": "At Pickup Warehouse",
    "Local Import": "At Origin Warehouse",
    "Local Export": "At Origin Warehouse",
}

_MOVE_TYPE_LOADED_LEG = {
    "Import": "Container Picked Up",
    "Export": "Container Loaded",
    "Local Import": "Loaded / Picked Up",
    "Local Export": "Loaded / Picked Up",
}

_MOVE_TYPE_ENROUTE_DELIVERY_LEG = {
    "Import": "En Route to Delivery Warehouse",
    "Export": "En Route to Port",
    "Local Import": "En Route to Destination Warehouse",
    "Local Export": "En Route to Destination Warehouse",
}

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

_DIRECT_MAP = {
    "Ready to Dispatch": "Ready to Dispatch",
    "Driver Assigned": "Driver Assigned",
    "Assigned": "Driver Assigned",
}


def map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]:
    """Map a legacy loads.status value to (new operational status, closeout_stage).

    Returns ("", "Not Started") for statuses that predate operational
    dispatch (order intake/verification) — those don't belong on
    loads.status in the new model; callers decide what to do with them
    (this function only defines the mapping, it doesn't migrate rows).
    """
    move_type = normalize_service_flow(move_type, default="Local Import")
    status = (old_status or "").strip()

    if status == "Cancelled":
        return "Cancelled", "Not Started"

    if status in _PRE_DISPATCH_STATUSES:
        return "", "Not Started"

    if status in _DIRECT_MAP:
        return _DIRECT_MAP[status], "Not Started"

    if status in ("Dispatched", "En Route to Pickup"):
        return _MOVE_TYPE_ENROUTE_PICKUP_LEG[move_type], "Not Started"

    if status == "At Port":
        return "At Port", "Not Started"

    if status == "At Pickup":
        return _MOVE_TYPE_AT_PICKUP_LEG[move_type], "Not Started"

    if status in ("Loaded / Picked Up", "Loaded"):
        return _MOVE_TYPE_LOADED_LEG[move_type], "Not Started"

    if status == "En Route To Delivery":
        return _MOVE_TYPE_ENROUTE_DELIVERY_LEG[move_type], "Not Started"

    if status == "Delivered":
        return "Delivered", "POD Needed"

    if status == "Returning Empty":
        return "Returning Empty", "POD Needed"

    if status == "POD Received":
        return "Dispatch Complete", "POD Received"

    if status in ("Ready for ProfitTools", "Exported to ProfitTools"):
        return "Dispatch Complete", "Ready for ProfitTools"

    if status in ("Invoiced", "Closed"):
        return "Dispatch Complete", "Closed"

    return "", "Not Started"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_legacy_status.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_legacy_status.py tests/test_dispatch_legacy_status.py
git commit -m "Add legacy status mapping for the dispatch redesign"
```

---

### Task 5: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all prior tests plus this plan's ~35 new tests pass.

- [ ] **Step 3: Report status**

This plan produces no user-visible change — `dispatch_board.py` still uses
the old free-selectbox status update path. That wiring, plus the exception
service and the intake/billing workspace split, are separate follow-up
plans (Phase 4/5 of the original request), scoped after this backend layer
is reviewed.
