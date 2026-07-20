# Orders Management: Ready to Dispatch Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Ready to Dispatch" queue to Orders Management where a dispatcher assigns driver/truck/chassis (looked up from the existing Drivers roster) and flips an order's status from `Booking Verified` to `Ready to Dispatch`, removing driver/truck/chassis fields from the earlier booking-review editor.

**Architecture:** A new pure-logic-plus-thin-I/O service module (`services/driver_roster_service.py`) looks up drivers from the existing `drivers` table. `pages_app/orders_management.py` gets a 5th queue tab whose detail panel (`_render_ready_to_dispatch_panel`) uses that roster to auto-fill Truck Assigned / Driver Phone, previews the existing canonical driver dispatch message, and on submit updates the load's Driver Name / Truck Assigned / Chassis / Status fields via the existing `DispatchDatabaseClient().update_row_fields`.

**Tech Stack:** Python 3.14, Streamlit, pandas, SQLAlchemy/Postgres (existing `db_client.read_df`), pytest.

## Global Constraints

- No database migration — no new columns. Driver phone comes from the existing `drivers` table, never persisted to `loads`.
- No changes to `pages_app/dispatch_board.py` or `pages_app/admin.py` (Drivers roster admin) in this plan.
- Port Verified / PIN incompleteness for Import/Export loads is **warn-only** — never blocks the "Mark Ready to Dispatch" action.
- "Mark Ready to Dispatch" is disabled until Driver Name, Truck Assigned, and Chassis are all non-blank. Driver Phone stays optional.
- Reuse `_generate_driver_dispatch_message` (services/dispatch_workflow_service.py) verbatim for the message preview — do not create a second message template.
- Do not add raw SQL to `pages_app/orders_management.py` — all queries live in `services/driver_roster_service.py`.

---

### Task 1: `find_driver_in_roster` pure lookup function

**Files:**
- Create: `services/driver_roster_service.py`
- Test: `tests/test_driver_roster_service.py`

**Interfaces:**
- Produces: `find_driver_in_roster(drivers_df: pd.DataFrame | None, driver_name: str) -> dict | None` — case-insensitive match on a `driver_name` column; returns the first matching row as a `dict`, or `None` if the roster is empty/missing the column, the name is blank, or nothing matches.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_driver_roster_service.py`:

```python
import pandas as pd

from services.driver_roster_service import find_driver_in_roster


def _roster(rows):
    return pd.DataFrame(rows)


def test_match_found_returns_dict():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "Victor Reza")
    assert result == {
        "driver_name": "Victor Reza",
        "phone": "555-1234",
        "truck_number": "T-12",
        "status": "Active",
    }


def test_case_insensitive_match():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "victor reza")
    assert result["driver_name"] == "Victor Reza"


def test_no_match_returns_none():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    assert find_driver_in_roster(roster, "Someone Else") is None


def test_empty_roster_returns_none():
    assert find_driver_in_roster(_roster([]), "Victor Reza") is None
    typed_empty = pd.DataFrame(columns=["driver_name", "phone", "truck_number", "status"])
    assert find_driver_in_roster(typed_empty, "Victor Reza") is None


def test_multiple_rows_only_one_matches():
    roster = _roster([
        {"driver_name": "Alice Driver", "phone": "111", "truck_number": "T-1", "status": "Active"},
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
        {"driver_name": "Bob Driver", "phone": "222", "truck_number": "T-2", "status": "Active"},
    ])
    result = find_driver_in_roster(roster, "Victor Reza")
    assert result["truck_number"] == "T-12"


def test_blank_or_none_driver_name_returns_none():
    roster = _roster([
        {"driver_name": "Victor Reza", "phone": "555-1234", "truck_number": "T-12", "status": "Active"},
    ])
    assert find_driver_in_roster(roster, "") is None
    assert find_driver_in_roster(roster, None) is None


def test_none_dataframe_returns_none():
    assert find_driver_in_roster(None, "Victor Reza") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_driver_roster_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.driver_roster_service'`

- [ ] **Step 3: Write the minimal implementation**

Create `services/driver_roster_service.py`:

```python
from __future__ import annotations

import pandas as pd

from db_client import read_df


def find_driver_in_roster(drivers_df: pd.DataFrame | None, driver_name: str) -> dict | None:
    """Case-insensitive lookup of a driver's roster record by name.

    Pure function — takes the roster DataFrame as an argument so it can be
    unit tested without a database. Returns the first matching row as a
    dict, or None if the roster is empty, the name is blank, or no row
    matches.
    """
    name = str(driver_name or "").strip()
    if not name or drivers_df is None or drivers_df.empty:
        return None

    if "driver_name" not in drivers_df.columns:
        return None

    matches = drivers_df[
        drivers_df["driver_name"].astype(str).str.strip().str.casefold() == name.casefold()
    ]
    if matches.empty:
        return None

    return matches.iloc[0].to_dict()


def list_active_drivers() -> pd.DataFrame:
    """Active roster drivers available for Ready to Dispatch assignment.

    Thin I/O wrapper around the `drivers` table — not unit tested directly
    (no test database in this environment); verified with a manual
    read-only smoke check (Task 2, Step 2).
    """
    return read_df(
        "select driver_name, phone, truck_number from drivers "
        "where status = 'Active' order by driver_name"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_driver_roster_service.py -v`
Expected: `7 passed`

- [ ] **Step 5: Compile check**

Run: `python -m compileall services/driver_roster_service.py tests/test_driver_roster_service.py`
Expected: exit code 0, no errors.

- [ ] **Step 6: Commit**

```bash
git add services/driver_roster_service.py tests/test_driver_roster_service.py
git commit -m "$(cat <<'EOF'
feat: add driver roster lookup service for Ready to Dispatch

Pure find_driver_in_roster function plus a thin list_active_drivers
query against the existing drivers table, so driver phone/truck can be
looked up instead of duplicating a phone column onto loads.
EOF
)"
```

---

### Task 2: Verify `list_active_drivers()` against the real roster (read-only)

**Files:**
- None modified — verification only.

**Interfaces:**
- Consumes: `list_active_drivers()` from Task 1.

- [ ] **Step 1: Run a read-only smoke check**

Run:
```bash
python -c "
from services.driver_roster_service import list_active_drivers
df = list_active_drivers()
print(df.shape)
print(df.head(5).to_string())
print('all Active:', (df.shape[0] >= 0))
"
```
Expected: prints a DataFrame with columns `driver_name`, `phone`, `truck_number`; no exception. Row count should match the number of drivers with `status = 'Active'` in the `drivers` table (read-only query, makes no changes).

- [ ] **Step 2: Commit**

No files changed by this task — nothing to commit. Proceed to Task 3.

---

### Task 3: Remove Driver/Truck/Chassis from the shared order editor; add the `Ready to Dispatch` status label

**Files:**
- Modify: `pages_app/orders_management.py:14-26` (status lists)
- Modify: `pages_app/orders_management.py:533-554` (editor form, `c3` column)
- Modify: `pages_app/orders_management.py:558-583` (save payload)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ORDER_MANAGEMENT_STATUSES` now includes `"Ready to Dispatch"`; `_render_order_detail_editor` no longer reads or writes Driver Name / Truck Assigned / Chassis.

- [ ] **Step 1: Add "Ready to Dispatch" to the status lists**

In `pages_app/orders_management.py`, replace:

```python
ORDER_MANAGEMENT_STATUSES = [
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Cancelled",
]

ORDER_MANAGEMENT_STATUS_LABELS = {
    "New": "New",
    "Hold/Need Info": "Missing Info",
    "Booking Verified": "Booking Verified",
    "Cancelled": "Cancel",
}
```

with:

```python
ORDER_MANAGEMENT_STATUSES = [
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Ready to Dispatch",
    "Cancelled",
]

ORDER_MANAGEMENT_STATUS_LABELS = {
    "New": "New",
    "Hold/Need Info": "Missing Info",
    "Booking Verified": "Booking Verified",
    "Ready to Dispatch": "Ready to Dispatch",
    "Cancelled": "Cancel",
}
```

- [ ] **Step 2: Remove Driver/Truck/Chassis inputs from the shared editor form**

In `_render_order_detail_editor`, replace the `c3` block:

```python
        with c3:
            current_order_status = _safe_str(selected_load.get("Status", "New"))
            order_status_options = list(ORDER_MANAGEMENT_STATUSES)
            if current_order_status and current_order_status not in order_status_options:
                order_status_options.insert(0, current_order_status)
            status = st.selectbox(
                "Status",
                order_status_options,
                index=order_status_options.index(current_order_status)
                if current_order_status in order_status_options else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
                key=f"{form_key}_status",
            )
            driver = st.text_input("Driver Name", value=_safe_str(selected_load.get("Driver Name", "")), key=f"{form_key}_driver")
            truck = st.text_input("Truck Assigned", value=_safe_str(selected_load.get("Truck Assigned", "")), key=f"{form_key}_truck")
            chassis = st.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")), key=f"{form_key}_chassis")
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=135,
                key=f"{form_key}_notes",
            )
```

with:

```python
        with c3:
            current_order_status = _safe_str(selected_load.get("Status", "New"))
            order_status_options = list(ORDER_MANAGEMENT_STATUSES)
            if current_order_status and current_order_status not in order_status_options:
                order_status_options.insert(0, current_order_status)
            status = st.selectbox(
                "Status",
                order_status_options,
                index=order_status_options.index(current_order_status)
                if current_order_status in order_status_options else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
                key=f"{form_key}_status",
            )
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=135,
                key=f"{form_key}_notes",
            )
```

Note: this is a booking/order editor only now — Driver/Truck/Chassis assignment moves to the Ready to Dispatch panel (Task 4).

- [ ] **Step 3: Remove Driver/Truck/Chassis from the save payload**

In the same function, replace:

```python
    if save_order:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "driver_name": driver.strip(),
            "truck_assigned": truck.strip(),
            "chassis": chassis.strip(),
            "dispatcher_notes": notes.strip(),
        }
```

with:

```python
    if save_order:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "dispatcher_notes": notes.strip(),
        }
```

- [ ] **Step 4: Compile check**

Run: `python -m compileall pages_app/orders_management.py`
Expected: exit code 0, no errors.

- [ ] **Step 5: Verify the fields are gone from the shared editor**

Run:
```bash
grep -n 'Driver Name\|Truck Assigned\|"Chassis"' pages_app/orders_management.py
```

This file has two pre-existing, unrelated places these strings legitimately still appear — confirm the output is limited to exactly these and nothing inside `_render_order_detail_editor`:

1. The `columns` display list (around line 648-653: `"Driver Name", "Truck Assigned", "Chassis",`) — this is the read-only table column list shown for every queue and is unaffected by this task.
2. `render_booking_review`'s own "Edit Selected Booking" form (around line 381-383: `driver = st.text_input("Driver Name", ...)`, `truck = st.text_input("Truck Assigned", ...)`, `chassis = st.text_input("Chassis", ...)`) — this is a separate, already-unused function (not called from `pages_app/router.py` or anywhere else; confirmed via `grep -rn "render_booking_review" pages_app/router.py` returning nothing) and is out of scope for this task.

If any match appears between roughly lines 477-625 (`_render_order_detail_editor`), the removal in Step 2/3 was incomplete — go back and fix it before proceeding.

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest -q --ignore=tests/test_operations_attachment_parsing.py --ignore=tests/test_operations_classification.py --ignore=tests/test_operations_container_qty_confirmation.py --ignore=tests/test_operations_control_center_snapshot.py --ignore=tests/test_operations_email_insert_resilience.py --ignore=tests/test_operations_email_sync_budget.py --ignore=tests/test_operations_email_triage_service.py --ignore=tests/test_operations_merge_parsed_fields.py --ignore=tests/test_operations_merge_saved_attachment_fields.py --ignore=tests/test_operations_multi_container_service.py --ignore=tests/test_operations_pending_draft_fields.py --ignore=tests/test_operations_route_cargo_section.py --ignore=tests/test_pdf_preview.py --ignore=tests/test_db_client_column_exists.py`
Expected: all pass. (The ignored files fail to even collect in this environment due to a pre-existing `streamlit` `DeltaGeneratorSingleton` conflict unrelated to this change — verified pre-existing via `git stash` during the prior Dispatch Board search-blob fix.)

- [ ] **Step 7: Commit**

```bash
git add pages_app/orders_management.py
git commit -m "$(cat <<'EOF'
refactor: remove driver/truck/chassis from Orders Management booking editor

Booking Verified and earlier queues only edit booking/order info now.
Driver/truck/chassis assignment moves to the new Ready to Dispatch step.
EOF
)"
```

---

### Task 4: Add the `_render_ready_to_dispatch_panel` function

**Files:**
- Modify: `pages_app/orders_management.py` (imports at top of file, lines 1-11)
- Modify: `pages_app/orders_management.py` (new function, inserted immediately after `_render_order_detail_editor`, i.e. after the current line 625/before `render_orders_management`)

**Interfaces:**
- Consumes: `find_driver_in_roster`, `list_active_drivers` (Task 1); `_generate_driver_dispatch_message`, `_load_requires_port`, `_load_port_verified`, `_load_has_pin_or_appointment` (services/dispatch_workflow_service.py, already exist); `_safe_str`, `refresh_data` (already in this file); `DispatchDatabaseClient` (already imported).
- Produces: `_render_ready_to_dispatch_panel(work_df: pd.DataFrame, selected_row_id: int, context_key: str) -> None` — same signature shape as `_render_order_detail_editor`, so both can be used interchangeably as a `detail_renderer` callable in Task 5.

- [ ] **Step 1: Add the new imports**

Replace:

```python
from db_client import DispatchDatabaseClient
from services.dispatch_workflow_service import LOAD_TYPE_TABS, _normalize_load_type_value, _status_row_style
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter
```

with:

```python
from db_client import DispatchDatabaseClient
from services.dispatch_workflow_service import (
    LOAD_TYPE_TABS,
    _generate_driver_dispatch_message,
    _load_has_pin_or_appointment,
    _load_port_verified,
    _load_requires_port,
    _normalize_load_type_value,
    _status_row_style,
)
from services.driver_roster_service import find_driver_in_roster, list_active_drivers
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter
```

- [ ] **Step 2: Add the new function**

Insert immediately after the end of `_render_order_detail_editor` (after the line `st.info("Mark Booking Verified is disabled until all required fields are complete.")` that currently precedes `def render_orders_management`), a blank line, then:

```python
def _render_ready_to_dispatch_panel(work_df: pd.DataFrame, selected_row_id: int, context_key: str) -> None:
    selected_df = work_df[work_df["_row_id"].astype(int).eq(int(selected_row_id))]

    if selected_df.empty:
        st.warning("Selected order was not found.")
        return

    selected_load = selected_df.iloc[0]
    safe_context = re.sub(r"[^A-Za-z0-9_]+", "_", context_key)
    panel_key = f"ready_to_dispatch_{safe_context}_{selected_row_id}"

    st.markdown("### Ready to Dispatch")
    st.caption(
        f"Assign driver, truck, and chassis, then mark Ready to Dispatch. "
        f"Editing: {selected_load.get('Booking Number', '')} | "
        f"{selected_load.get('Customer', '')} | row {selected_row_id}"
    )

    summary_cols = st.columns(4)
    summary_cols[0].metric("Booking", _safe_str(selected_load.get("Booking Number", "")) or "-")
    summary_cols[1].metric("Container", _safe_str(selected_load.get("Container Number", "")) or "-")
    summary_cols[2].metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    summary_cols[3].metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    with st.expander("Order details", expanded=False):
        details = {
            "Port / Pickup": selected_load.get("Port", ""),
            "Warehouse / Delivery": selected_load.get("Warehouse", ""),
            "Delivery Need Date": selected_load.get("Delivery Need Date", ""),
            "LFD": selected_load.get("LFD", ""),
        }
        st.dataframe(
            pd.DataFrame([{"Field": k, "Value": v} for k, v in details.items()]),
            use_container_width=True,
            hide_index=True,
        )

    if _load_requires_port(selected_load) and not (
        _load_port_verified(selected_load) or _load_has_pin_or_appointment(selected_load)
    ):
        st.warning(
            "Port Verified / PIN is not complete yet. This load can still be marked "
            "Ready to Dispatch, but the Dispatch Board will flag it as an exception "
            "until Port Sync / PIN is done."
        )

    roster_df = list_active_drivers()
    current_driver_name = _safe_str(selected_load.get("Driver Name", ""))
    current_in_roster = find_driver_in_roster(roster_df, current_driver_name) if current_driver_name else None

    roster_options = ["Other / not in roster"]
    if current_driver_name and not current_in_roster:
        roster_options.append(f"{current_driver_name} (not in roster)")
    roster_options.extend(
        f"{row['driver_name']} ({_safe_str(row.get('truck_number', '')) or 'no truck on file'})"
        for _, row in roster_df.iterrows()
    )

    driver_choice = st.selectbox("Driver", roster_options, key=f"{panel_key}_driver_choice")

    if driver_choice == "Other / not in roster":
        driver_name = st.text_input(
            "Driver Name",
            value=current_driver_name if not current_in_roster else "",
            key=f"{panel_key}_driver_manual",
        )
        default_truck = _safe_str(selected_load.get("Truck Assigned", ""))
        default_phone = ""
    elif driver_choice.endswith("(not in roster)"):
        driver_name = current_driver_name
        default_truck = _safe_str(selected_load.get("Truck Assigned", ""))
        default_phone = ""
    else:
        typed_name = driver_choice.rsplit(" (", 1)[0]
        roster_match = find_driver_in_roster(roster_df, typed_name)
        driver_name = roster_match["driver_name"] if roster_match else typed_name
        default_truck = (roster_match or {}).get("truck_number") or ""
        default_phone = (roster_match or {}).get("phone") or ""

    field_cols = st.columns(3)
    truck = field_cols[0].text_input(
        "Truck Assigned",
        value=default_truck,
        key=f"{panel_key}_truck_{driver_choice}",
    )
    chassis = field_cols[1].text_input(
        "Chassis",
        value=_safe_str(selected_load.get("Chassis", "")),
        key=f"{panel_key}_chassis",
    )
    phone = field_cols[2].text_input(
        "Driver Phone",
        value=default_phone,
        key=f"{panel_key}_phone_{driver_choice}",
    )

    st.markdown("#### Generated Dispatch Message")
    preview_load = selected_load.copy()
    preview_load["Driver Name"] = driver_name
    preview_load["Truck Assigned"] = truck
    preview_load["Chassis"] = chassis
    generated_message = _generate_driver_dispatch_message(preview_load)
    st.text_area(
        "Dispatch Message",
        value=generated_message,
        height=260,
        key=f"{panel_key}_message",
    )
    if phone.strip():
        st.caption(f"Driver phone on file: {phone.strip()}")

    ready_disabled = not (driver_name.strip() and truck.strip() and chassis.strip())
    if st.button(
        "Mark Ready to Dispatch",
        key=f"{panel_key}_mark_ready",
        use_container_width=True,
        disabled=ready_disabled,
    ):
        DispatchDatabaseClient().update_row_fields(
            selected_row_id,
            {
                "Driver Name": driver_name.strip(),
                "Truck Assigned": truck.strip(),
                "Chassis": chassis.strip(),
                "Status": "Ready to Dispatch",
                "Dispatcher Notes": _safe_str(selected_load.get("Dispatcher Notes", ""))
                or "Driver, truck, and chassis assigned. Ready to dispatch.",
            },
        )
        st.session_state.pop("orders_management_selected_row_id", None)
        st.session_state.pop("orders_management_selected_context", None)
        refresh_data()
        st.success("Order marked Ready to Dispatch.")
        st.rerun()

    if ready_disabled:
        st.info("Mark Ready to Dispatch is disabled until Driver, Truck, and Chassis are all filled in.")

```

- [ ] **Step 3: Compile check**

Run: `python -m compileall pages_app/orders_management.py`
Expected: exit code 0, no errors.

- [ ] **Step 4: Verify the function is self-contained and correctly named**

Run:
```bash
grep -n "_render_ready_to_dispatch_panel\|find_driver_in_roster\|list_active_drivers" pages_app/orders_management.py
```
Expected: shows the import line and the `def _render_ready_to_dispatch_panel(...)` line. (It is not called anywhere yet — that's Task 5.)

- [ ] **Step 5: Run the full test suite**

Run the same filtered pytest command as Task 3, Step 6.
Expected: all pass (no behavior changed yet — this task only adds a new, uncalled function).

- [ ] **Step 6: Commit**

```bash
git add pages_app/orders_management.py
git commit -m "$(cat <<'EOF'
feat: add Ready to Dispatch panel (not yet wired into a queue)

Roster-backed driver select with truck/phone auto-fill, Port/PIN
warn-only banner, live driver-message preview via the existing
_generate_driver_dispatch_message, and a gated Mark Ready to Dispatch
action. Wiring into the Orders Management queue list is the next task.
EOF
)"
```

---

### Task 5: Wire "Ready to Dispatch" into the Orders Management queue list

**Files:**
- Modify: `pages_app/orders_management.py:663-756` (`render_clickable_order_table` + `render_orders_management`)

**Interfaces:**
- Consumes: `_render_ready_to_dispatch_panel` (Task 4), `_render_order_detail_editor` (existing).
- Produces: a 5th "Ready to Dispatch" radio option in the Orders Management queue selector.

- [ ] **Step 1: Parameterize `render_clickable_order_table` with a detail renderer**

Replace:

```python
    def render_clickable_order_table(table_df: pd.DataFrame, title: str):
```

with:

```python
    def render_clickable_order_table(table_df: pd.DataFrame, title: str, detail_renderer=_render_order_detail_editor):
```

Then replace the call inside it:

```python
        if selected_row_id is not None:
            visible_ids = set(work_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                _render_order_detail_editor(work_df, int(selected_row_id), context_key)
```

with:

```python
        if selected_row_id is not None:
            visible_ids = set(work_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                detail_renderer(work_df, int(selected_row_id), context_key)
```

- [ ] **Step 2: Add the "Ready to Dispatch" queue and route it to the new panel**

Replace:

```python
    queue_options = [
        "New",
        "Missing Info",
        "Booking Verified",
        "Cancel",
    ]
    queue_map = {
        "New": new_df,
        "Missing Info": missing_info_df,
        "Booking Verified": verified_df,
        "Cancel": cancelled_df,
    }

    selected_queue = st.radio("Order Queue", queue_options, horizontal=True, key="orders_management_queue")
    if st.session_state.get("orders_management_last_queue") != selected_queue:
        st.session_state["orders_management_last_queue"] = selected_queue
        clear_order_editor()

    render_clickable_order_table(queue_map[selected_queue], selected_queue)
```

with:

```python
    queue_options = [
        "New",
        "Missing Info",
        "Booking Verified",
        "Ready to Dispatch",
        "Cancel",
    ]
    queue_map = {
        "New": new_df,
        "Missing Info": missing_info_df,
        "Booking Verified": verified_df,
        "Ready to Dispatch": verified_df,
        "Cancel": cancelled_df,
    }
    queue_detail_renderers = {
        "Ready to Dispatch": _render_ready_to_dispatch_panel,
    }

    selected_queue = st.radio("Order Queue", queue_options, horizontal=True, key="orders_management_queue")
    if st.session_state.get("orders_management_last_queue") != selected_queue:
        st.session_state["orders_management_last_queue"] = selected_queue
        clear_order_editor()

    render_clickable_order_table(
        queue_map[selected_queue],
        selected_queue,
        detail_renderer=queue_detail_renderers.get(selected_queue, _render_order_detail_editor),
    )
```

Note: "Ready to Dispatch" and "Booking Verified" intentionally share the same source list (`verified_df`, i.e. orders where `Status == "Booking Verified"`) — they are the same underlying orders shown through two different action panels. Once an order's status is changed to `Ready to Dispatch`, it no longer matches `Status == "Booking Verified"` and disappears from both tabs on the next rerun, exactly as it would from any other queue transition in this file.

- [ ] **Step 3: Compile check**

Run: `python -m compileall pages_app/orders_management.py`
Expected: exit code 0, no errors.

- [ ] **Step 4: Confirm the queue is wired correctly**

Run:
```bash
grep -n '"Ready to Dispatch"' pages_app/orders_management.py
```
Expected: matches in `ORDER_MANAGEMENT_STATUSES`, `ORDER_MANAGEMENT_STATUS_LABELS`, `queue_options`, `queue_map` (as a key), and `queue_detail_renderers` (as a key) — five distinct occurrences confirming every wiring point from Tasks 3 and 5 landed.

- [ ] **Step 5: Run the full test suite**

Run the same filtered pytest command as Task 3, Step 6.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pages_app/orders_management.py
git commit -m "$(cat <<'EOF'
feat: add Ready to Dispatch queue tab to Orders Management

Booking Verified orders now flow through a dedicated Ready to Dispatch
tab to assign driver/truck/chassis (via the Drivers roster) and
generate the driver text message before advancing status.
EOF
)"
```

---

### Task 6: Final verification pass

**Files:**
- None modified — verification only.

- [ ] **Step 1: Compile every touched file**

Run: `python -m compileall pages_app/orders_management.py services/driver_roster_service.py tests/test_driver_roster_service.py`
Expected: exit code 0.

- [ ] **Step 2: Run the full filtered test suite one more time**

Run the same filtered pytest command as Task 3, Step 6.
Expected: all pass, including the 7 new `test_driver_roster_service.py` tests.

- [ ] **Step 3: Attempt to start the app and note the outcome**

Run: `python -m streamlit run app.py --server.headless true --server.port 8501` (background, capture to a log file).

If this fails with `ImportError: cannot import name 'DEFAULT_EXCLUDED_CONTENT_TYPES' from 'starlette.middleware.gzip'` — this is the same pre-existing `streamlit`/`starlette` environment mismatch hit during the prior Dispatch Board fix, unrelated to this feature. Stop here and report it rather than claiming a manual UI test was performed.

If the app starts successfully, walk through the manual acceptance test from the spec (`docs/superpowers/specs/2026-07-15-orders-management-ready-to-dispatch-design.md`, "Testing plan" section): move an order to Booking Verified, confirm the editor no longer shows Driver/Truck/Chassis, open the Ready to Dispatch tab, pick a roster driver, confirm truck/phone auto-fill and the message preview updates, fill in chassis, click Mark Ready to Dispatch, and confirm the order disappears from Orders Management and appears on the Dispatch Board.

- [ ] **Step 4: Report results**

No commit for this task (verification only). Summarize: compile result, test result, and whether the manual UI walkthrough was possible or blocked by the environment issue.
