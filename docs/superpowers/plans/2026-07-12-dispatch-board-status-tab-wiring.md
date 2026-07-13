# Dispatch Board Status Tab Wiring Implementation Plan (Phase 4, part 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Dispatch Board's "Status Update" tab call `dispatch_transition_service.apply_transition()` instead of writing `loads.status` directly through a free, unvalidated selectbox — without breaking loads that haven't reached "Ready to Dispatch" yet (those still go through the pre-existing free-status path, since their workspace/redesign is Phase 5, not this plan).

**Architecture:** `render_dispatch_workspace()`'s Status Update tab branches on whether the load's *current* status is already a recognized operational stage (per `dispatch_stages.get_operational_stages()`). If yes: the selectbox is restricted to that move type's valid operational stages, and saving calls `apply_transition()`. If no (still a pre-dispatch/legacy status not yet in the new model): the existing free-selectbox behavior is left completely unchanged. This is the minimal-risk way to adopt the new service without requiring Phase 5's intake/verification workspace split to happen first.

**Tech Stack:** Streamlit, existing `services.dispatch_transition_service`, `services.dispatch_stages`.

## Global Constraints

- Do not change `render_dispatch_workspace()`'s function signature or any other tab.
- Do not touch the pre-dispatch (legacy) status path's behavior — it must render and behave byte-for-byte as it does today, since Phase 5 (which relocates those loads to their own workspace) hasn't happened yet.
- Driver/Truck/Chassis field updates must be written *before* the transition is validated, so "assign a driver and mark Driver Assigned in the same click" works (the transition check re-reads the load from the DB).
- The transition service already sends no customer email itself — the existing `_send_customer_status_update_email()` call stays in the page, called only after a successful transition, exactly as today.

---

### Task 1: Wire the operational-status branch

**Files:**
- Modify: `pages_app/dispatch_board.py` (imports, and the `status_tab` block inside `render_dispatch_workspace()`, currently lines ~199-261)

**Interfaces:**
- Consumes: `services.dispatch_stages.get_operational_stages`, `services.dispatch_stages.CANCELLED_STATUS`, `services.dispatch_transition_service.apply_transition`, `_normalize_load_type` (already imported from `services.dispatch_workflow_service`).

- [ ] **Step 1: Add imports**

Add near the existing `from services.dispatch_workflow_service import (...)` block in `pages_app/dispatch_board.py`:
```python
from services.dispatch_stages import CANCELLED_STATUS, get_operational_stages
from services.dispatch_transition_service import apply_transition
```

- [ ] **Step 2: Replace the `status_tab` block**

Current code (verify exact match before replacing, this file has been edited several times this session):
```python
    with status_tab:
        st.markdown("### Status Update")
        c1, c2, c3, c4 = st.columns(4)
        current_status = str(selected_load.get("Status", "") or "New")
        status_index = LOAD_STATUS_FLOW.index(current_status) if current_status in LOAD_STATUS_FLOW else 0

        new_status = c1.selectbox("New Status", LOAD_STATUS_FLOW, index=status_index)
        driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""))
        truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""))
        chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""))
        customer_email = st.text_input(
            "Customer Email",
            value=str(selected_load.get("Customer Email", "") or ""),
            key=f"customer_email_{load_id}",
    )

        note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120)

        if st.button("Save Status Update", key=f"save_status_{load_id}"):
            if (
                new_status in ["Ready to Dispatch", "Dispatched"]
                and new_status != current_status
                and not readiness.get("dispatchable")
            ):
                st.error("This load cannot be marked Ready to Dispatch or Dispatched until order details, port verification, driver, truck, and PIN/appointment are complete.")
                return
            updates = {}
            if new_status != current_status:
                updates["Status"] = new_status
            if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
                updates["Driver Name"] = driver.strip()
            if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
                updates["Truck Assigned"] = truck.strip()
            if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
                updates["Chassis"] = chassis.strip()
            if note.strip() != str(selected_load.get("Dispatcher Notes", "") or "").strip():
                updates["Dispatcher Notes"] = note.strip()
           

            if updates:
                DispatchDatabaseClient().update_row_fields(load_id, updates)

                if "Status" in updates:
                    email_sent, email_msg = _send_customer_status_update_email(
                        load_id,
                        selected_load,
                        current_status,
                        new_status,
                        note.strip(),
                        customer_email.strip(),
                    )

                    if email_sent:
                        st.success(f"Status updated. {email_msg}")
                    else:
                        st.warning(f"Status updated, but customer email was not sent: {email_msg}")
                else:
                    st.success("Load details updated.")

                _run_refresh(refresh_callback)
                st.rerun()
            else:
                st.info("No changes detected.")
```

Replace with:
```python
    with status_tab:
        st.markdown("### Status Update")
        current_status = str(selected_load.get("Status", "") or "New")
        move_type = _normalize_load_type(selected_load)
        operational_stages = get_operational_stages(move_type)

        if current_status in operational_stages or current_status == CANCELLED_STATUS:
            _render_operational_status_tab(
                selected_load, load_id, current_status, operational_stages, refresh_callback
            )
        else:
            _render_legacy_status_tab(selected_load, load_id, current_status, refresh_callback)
```

- [ ] **Step 3: Add the two extracted tab-body functions**

Add these two new module-level functions in `pages_app/dispatch_board.py`, placed just above `render_dispatch_workspace` (so they're defined before use):

```python
def _render_operational_status_tab(selected_load, load_id: int, current_status: str, operational_stages: list[str], refresh_callback) -> None:
    status_options = operational_stages + [CANCELLED_STATUS]
    current_index = status_options.index(current_status) if current_status in status_options else 0

    c1, c2, c3, c4 = st.columns(4)
    new_status = c1.selectbox("New Status", status_options, index=current_index, key=f"new_status_{load_id}")
    driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""), key=f"status_driver_{load_id}")
    truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""), key=f"status_truck_{load_id}")
    chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""), key=f"status_chassis_{load_id}")
    customer_email = st.text_input(
        "Customer Email",
        value=str(selected_load.get("Customer Email", "") or ""),
        key=f"customer_email_{load_id}",
    )
    note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120, key=f"status_note_{load_id}")

    override = st.checkbox("Override transition rules (requires a reason)", key=f"status_override_{load_id}")
    override_reason = ""
    if override:
        override_reason = st.text_input("Override reason", key=f"status_override_reason_{load_id}")

    if st.button("Save Status Update", key=f"save_status_{load_id}"):
        detail_updates = {}
        if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
            detail_updates["Driver Name"] = driver.strip()
        if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
            detail_updates["Truck Assigned"] = truck.strip()
        if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
            detail_updates["Chassis"] = chassis.strip()

        if detail_updates:
            DispatchDatabaseClient().update_row_fields(load_id, detail_updates)

        if new_status != current_status:
            result = apply_transition(
                load_id,
                new_status,
                note=note.strip(),
                override=override,
                override_reason=override_reason.strip(),
            )
            if not result["ok"]:
                st.error(result["reason"])
                return

            email_sent, email_msg = _send_customer_status_update_email(
                load_id, selected_load, current_status, new_status, note.strip(), customer_email.strip(),
            )
            if email_sent:
                st.success(f"Status updated. {email_msg}")
            else:
                st.warning(f"Status updated, but customer email was not sent: {email_msg}")

            _run_refresh(refresh_callback)
            st.rerun()
        elif detail_updates:
            st.success("Load details updated.")
            _run_refresh(refresh_callback)
            st.rerun()
        else:
            st.info("No changes detected.")


def _render_legacy_status_tab(selected_load, load_id: int, current_status: str, refresh_callback) -> None:
    """Unchanged pre-dispatch status path — loads not yet in the new
    operational model (Ready to Dispatch or later) keep the original free
    status selectbox until Phase 5 gives them their own Intake &
    Verification workspace."""
    c1, c2, c3, c4 = st.columns(4)
    status_index = LOAD_STATUS_FLOW.index(current_status) if current_status in LOAD_STATUS_FLOW else 0

    new_status = c1.selectbox("New Status", LOAD_STATUS_FLOW, index=status_index, key=f"legacy_status_{load_id}")
    driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""), key=f"legacy_driver_{load_id}")
    truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""), key=f"legacy_truck_{load_id}")
    chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""), key=f"legacy_chassis_{load_id}")
    customer_email = st.text_input(
        "Customer Email",
        value=str(selected_load.get("Customer Email", "") or ""),
        key=f"legacy_customer_email_{load_id}",
    )

    note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120, key=f"legacy_note_{load_id}")

    if st.button("Save Status Update", key=f"legacy_save_status_{load_id}"):
        readiness = _load_readiness_details(selected_load, documents_df=_read_documents_for_load(load_id))
        if (
            new_status in ["Ready to Dispatch", "Dispatched"]
            and new_status != current_status
            and not readiness.get("dispatchable")
        ):
            st.error("This load cannot be marked Ready to Dispatch or Dispatched until order details, port verification, driver, truck, and PIN/appointment are complete.")
            return
        updates = {}
        if new_status != current_status:
            updates["Status"] = new_status
        if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
            updates["Driver Name"] = driver.strip()
        if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
            updates["Truck Assigned"] = truck.strip()
        if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
            updates["Chassis"] = chassis.strip()
        if note.strip() != str(selected_load.get("Dispatcher Notes", "") or "").strip():
            updates["Dispatcher Notes"] = note.strip()

        if updates:
            DispatchDatabaseClient().update_row_fields(load_id, updates)

            if "Status" in updates:
                email_sent, email_msg = _send_customer_status_update_email(
                    load_id, selected_load, current_status, new_status, note.strip(), customer_email.strip(),
                )
                if email_sent:
                    st.success(f"Status updated. {email_msg}")
                else:
                    st.warning(f"Status updated, but customer email was not sent: {email_msg}")
            else:
                st.success("Load details updated.")

            _run_refresh(refresh_callback)
            st.rerun()
        else:
            st.info("No changes detected.")
```

Note: `_render_legacy_status_tab` is the exact original logic, unchanged, just extracted into its own function and given unique widget keys (`legacy_` prefix) so it can coexist with `_render_operational_status_tab` without Streamlit key collisions across reruns where a load's branch could change.

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
```
Expected: exit 0.

Visually: open Dispatch Board with the running app.
  - A load still in a pre-dispatch status (e.g. `New`, `Hold/Need Info`) should show the exact same Status Update tab as before (all 29 legacy statuses in the dropdown).
  - A load already at `Ready to Dispatch` or later should show only that move type's valid operational stages + Cancelled, and attempting an invalid jump (e.g. straight to `Dispatch Complete` from `Ready to Dispatch`) should show a clear error instead of silently saving.

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Wire Dispatch Board Status Update tab to dispatch_transition_service for operational-stage loads"
```

---

### Task 2: Full verification

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all 63 prior tests still pass (this plan adds no new tests — it's UI wiring over already-tested backend logic; the branch behavior is covered by manual verification in Task 1 Step 4).
