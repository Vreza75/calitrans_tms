# Multi-Container Booking Row Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Orders/Load Management and Dispatch Board, collapse `loads` rows that share a `parent_booking_key` into one summary row with a "N containers" badge; clicking it reveals the individual containers instead of jumping straight to a single-load editor.

**Architecture:** A new pure grouping function in `services/load_grouping_service.py` (no Streamlit/DB imports, directly unit-testable) is called by both page files right before they render their existing tables/cards. Both pages keep their current selection mechanisms (`st.dataframe` row-select for Orders Management, "Work Load" button + session state for Dispatch Board) — grouping only changes *what* gets shown at each step, not the underlying interaction pattern.

**Tech Stack:** pandas, Streamlit. No new dependencies.

## Global Constraints

- `parent_booking_key`, `container_sequence`, `container_total` come from `loads` (added by `database/multi_container_migration.sql`, already live in the database per this session's earlier work).
- Orders Management: any group sharing a non-null `parent_booking_key` always collapses.
- Dispatch Board: a group only collapses when every row in it has the same `Status` value — otherwise every row in that group renders individually, exactly as if ungrouped. Re-evaluated fresh on every render.
- Badge text: `f"{count} containers"` when count > 1, empty string when count == 1.
- Rows with `parent_booking_key` null/empty are never grouped with each other, even if that means comparing empty strings — group only by a genuinely shared non-empty key.
- Do not change `_render_order_detail_editor`, `render_dispatch_workspace`, `open_load_workspace_dialog`, or `create_container_work_orders` signatures or behavior — only what feeds into row/card selection ahead of them.

---

### Task 1: Expose grouping columns to the pages' dataframe

**Files:**
- Modify: `services/tms_data_service.py:29-51` (`EXT_LOAD_COLUMNS`), `services/tms_data_service.py:123-157` (`get_ext_df()`)

**Interfaces:**
- Produces: `parent_booking_key: str`, `container_sequence: int | None`, `container_total: int | None` columns now present in the dataframe returned by `load_tms_data()`, consumed by Task 2/3/4.

- [ ] **Step 1: Add the three columns to `EXT_LOAD_COLUMNS`**

In `services/tms_data_service.py`, change:
```python
EXT_LOAD_COLUMNS = [
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "chassis_provider",
    "pickup_reference",
    "delivery_reference",
    "invoice_status",
    "driver_pay_status",
    "customer_rate",
    "carrier_pay",
    "accessorials",
    "margin",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
]
```
to:
```python
EXT_LOAD_COLUMNS = [
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "chassis_provider",
    "pickup_reference",
    "delivery_reference",
    "invoice_status",
    "driver_pay_status",
    "customer_rate",
    "carrier_pay",
    "accessorials",
    "margin",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
    "parent_booking_key",
    "container_sequence",
    "container_total",
]
```

- [ ] **Step 2: Add the same three columns to the SQL `select` in `get_ext_df()`**

In `services/tms_data_service.py`, the `get_ext_df()` query currently ends:
```python
                live_load_status,
                live_unload_status,
                last_driver_update
            from loads
            """
        )
```
Change to:
```python
                live_load_status,
                live_unload_status,
                last_driver_update,
                parent_booking_key,
                container_sequence,
                container_total
            from loads
            """
        )
```

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q services/tms_data_service.py
```
Expected: exit 0.

Confirm the running app's log shows no new traceback after this change (Streamlit hot-reloads, or trigger a rerun in the browser).

- [ ] **Step 4: Commit**

```bash
git add services/tms_data_service.py
git commit -m "Expose parent_booking_key/container_sequence/container_total to the loads dataframe"
```

---

### Task 2: Grouping helper with tests

**Files:**
- Create: `services/load_grouping_service.py`
- Test: `tests/test_load_grouping_service.py`

**Interfaces:**
- Produces: `group_loads_by_booking(df: pd.DataFrame, *, require_same_status: bool = False) -> pd.DataFrame`. Output dataframe has all of the input's columns, plus `"Containers"` (str) and `"_grouped_row_ids"` (list[int]). One row per group (or per ungrouped original row).
- Consumes: input dataframe must have `"_row_id"`, `"parent_booking_key"`, and (when `require_same_status=True`) `"Status"` columns. Other columns pass through from the first row of each group unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_load_grouping_service.py`:
```python
import pandas as pd

from services.load_grouping_service import group_loads_by_booking


def _row(row_id, booking, parent_booking_key, status="New", customer="Acme"):
    return {
        "_row_id": row_id,
        "Booking Number": booking,
        "parent_booking_key": parent_booking_key,
        "Status": status,
        "Customer": customer,
    }


def test_four_containers_same_booking_collapse_to_one_row():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800"),
        _row(2, "RICGX1235800", "RICGX1235800"),
        _row(3, "RICGX1235800", "RICGX1235800"),
        _row(4, "RICGX1235800", "RICGX1235800"),
    ])
    result = group_loads_by_booking(df)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "4 containers"
    assert sorted(result.iloc[0]["_grouped_row_ids"]) == [1, 2, 3, 4]
    assert result.iloc[0]["Customer"] == "Acme"


def test_single_container_booking_passes_through_unchanged():
    df = pd.DataFrame([_row(1, "ABC123", "")])
    result = group_loads_by_booking(df)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == ""
    assert result.iloc[0]["_grouped_row_ids"] == [1]


def test_rows_with_no_parent_booking_key_are_never_grouped_together():
    df = pd.DataFrame([
        _row(1, "ABC123", ""),
        _row(2, "DEF456", ""),
    ])
    result = group_loads_by_booking(df)
    assert len(result) == 2


def test_mixed_status_group_does_not_collapse_when_require_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="Dispatched"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=True)
    assert len(result) == 2
    assert set(result["_row_id"]) == {1, 2}


def test_mixed_status_group_collapses_when_not_requiring_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="Dispatched"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=False)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "2 containers"


def test_same_status_group_collapses_when_require_same_status():
    df = pd.DataFrame([
        _row(1, "RICGX1235800", "RICGX1235800", status="New"),
        _row(2, "RICGX1235800", "RICGX1235800", status="New"),
    ])
    result = group_loads_by_booking(df, require_same_status=True)
    assert len(result) == 1
    assert result.iloc[0]["Containers"] == "2 containers"


def test_empty_dataframe_returns_empty_dataframe():
    df = pd.DataFrame(columns=["_row_id", "Booking Number", "parent_booking_key", "Status", "Customer"])
    result = group_loads_by_booking(df)
    assert result.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_load_grouping_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.load_grouping_service'`

- [ ] **Step 3: Implement `services/load_grouping_service.py`**

```python
from __future__ import annotations

import pandas as pd


def group_loads_by_booking(
    df: pd.DataFrame,
    *,
    require_same_status: bool = False,
) -> pd.DataFrame:
    """Collapse rows sharing a non-null parent_booking_key into one summary
    row per booking.

    Adds two columns to the result:
      - "Containers": "N containers" when a group has more than one row,
        "" for an ungrouped (single-row) booking.
      - "_grouped_row_ids": list[int] of the _row_id values folded into that
        summary row.

    Rows with an empty/missing parent_booking_key are never grouped with
    each other, even if several happen to share that same empty value —
    each such row is its own group of one.

    If require_same_status is True, a group only collapses when every row
    in it shares the same "Status" value; otherwise its rows are returned
    individually, exactly as if parent_booking_key were empty for them.
    """
    if df.empty:
        result = df.copy()
        result["Containers"] = pd.Series(dtype="object")
        result["_grouped_row_ids"] = pd.Series(dtype="object")
        return result

    working = df.copy()
    working["_parent_booking_key_clean"] = working.get(
        "parent_booking_key", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip()

    summary_rows = []

    for key, group in working.groupby(
        working["_parent_booking_key_clean"].where(
            working["_parent_booking_key_clean"] != "", other=working.index.astype(str)
        )
    ):
        if len(group) > 1 and require_same_status:
            statuses = group.get("Status", pd.Series(dtype="object")).astype(str).str.strip()
            if statuses.nunique() > 1:
                for _, row in group.iterrows():
                    single = row.drop(labels=["_parent_booking_key_clean"]).to_dict()
                    single["Containers"] = ""
                    single["_grouped_row_ids"] = [int(row["_row_id"])]
                    summary_rows.append(single)
                continue

        first = group.iloc[0].drop(labels=["_parent_booking_key_clean"]).to_dict()
        row_ids = [int(value) for value in group["_row_id"].tolist()]
        first["Containers"] = f"{len(group)} containers" if len(group) > 1 else ""
        first["_grouped_row_ids"] = row_ids
        summary_rows.append(first)

    return pd.DataFrame(summary_rows).reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_load_grouping_service.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add services/load_grouping_service.py tests/test_load_grouping_service.py
git commit -m "Add group_loads_by_booking() with tests"
```

---

### Task 3: Wire grouping into Orders/Load Management

**Files:**
- Modify: `pages_app/orders_management.py` (`render_clickable_order_table()`, around lines 662-698)

**Interfaces:**
- Consumes: `group_loads_by_booking` from `services.load_grouping_service`.
- No change to `_render_order_detail_editor(work_df, selected_row_id, context_key)`.

- [ ] **Step 1: Import the grouping helper**

Add near the top of `pages_app/orders_management.py`:
```python
from services.load_grouping_service import group_loads_by_booking
```

- [ ] **Step 2: Group before display, add the badge column**

In `render_clickable_order_table()`, currently:
```python
        display_cols = [c for c in columns if c in table_df.columns]
        sorted_type_df = table_df.sort_values("_row_id", ascending=False)
        context_key = f"{title}_{selected_flow}"
        styled_type_df = sorted_type_df[display_cols].style.apply(_status_row_style, axis=1)

        event = st.dataframe(
            styled_type_df,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key=f"orders_table_{title}_{selected_flow}",
        )

        selected_rows = event.selection.rows

        if selected_rows:
            selected_row_id = int(sorted_type_df.iloc[selected_rows[0]]["_row_id"])
            st.session_state["orders_management_selected_row_id"] = selected_row_id
            st.session_state["orders_management_selected_context"] = context_key

        selected_context = st.session_state.get("orders_management_selected_context")
        selected_row_id = st.session_state.get("orders_management_selected_row_id")

        if selected_context == context_key and selected_row_id is not None:
            visible_ids = set(sorted_type_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                _render_order_detail_editor(work_df, int(selected_row_id), context_key)
```

Replace with:
```python
        grouped_df = group_loads_by_booking(table_df)
        display_cols = [c for c in columns if c in grouped_df.columns] + ["Containers"]
        sorted_type_df = grouped_df.sort_values("_row_id", ascending=False)
        context_key = f"{title}_{selected_flow}"
        styled_type_df = sorted_type_df[display_cols].style.apply(_status_row_style, axis=1)

        event = st.dataframe(
            styled_type_df,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key=f"orders_table_{title}_{selected_flow}",
        )

        selected_rows = event.selection.rows

        if selected_rows:
            selected_group_ids = sorted_type_df.iloc[selected_rows[0]]["_grouped_row_ids"]
            st.session_state["orders_management_selected_group_ids"] = list(selected_group_ids)
            st.session_state["orders_management_selected_context"] = context_key
            st.session_state.pop("orders_management_selected_row_id", None)

        selected_context = st.session_state.get("orders_management_selected_context")
        selected_group_ids = st.session_state.get("orders_management_selected_group_ids")
        selected_row_id = st.session_state.get("orders_management_selected_row_id")

        if selected_context != context_key:
            return

        if selected_group_ids and len(selected_group_ids) > 1:
            st.divider()
            st.markdown(f"#### {len(selected_group_ids)} containers in this booking")
            containers_df = work_df[work_df["_row_id"].astype(int).isin(selected_group_ids)]
            container_cols = [c for c in ["_row_id", "Container Number", "Status", "Driver Name", "Delivery Need Date"] if c in containers_df.columns]
            container_event = st.dataframe(
                containers_df[container_cols],
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
                key=f"orders_table_containers_{context_key}",
            )
            if container_event.selection.rows:
                picked_row_id = int(containers_df.iloc[container_event.selection.rows[0]]["_row_id"])
                st.session_state["orders_management_selected_row_id"] = picked_row_id
                selected_row_id = picked_row_id

        if selected_row_id is not None:
            visible_ids = set(work_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                _render_order_detail_editor(work_df, int(selected_row_id), context_key)
```

Note: for an ungrouped row (`_grouped_row_ids` has length 1), `selected_row_id` is never set by this new code path — add one more branch so single-container bookings still work exactly as before: immediately after the `st.session_state.pop("orders_management_selected_row_id", None)` line above, when `len(selected_group_ids) == 1`, set `st.session_state["orders_management_selected_row_id"] = int(selected_group_ids[0])` instead of popping it.

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q pages_app/orders_management.py
```
Expected: exit 0.

Visually: open Orders/Load Management in the running app. A single-container order should behave exactly as before (click row → detail editor opens directly). If you have a multi-container test booking created via the Operations Inbox flow from earlier this session, confirm it shows one row with a "N containers" badge, and clicking it reveals the container sub-table before the detail editor.

- [ ] **Step 4: Commit**

```bash
git add pages_app/orders_management.py
git commit -m "Collapse multi-container bookings into one row on Orders/Load Management"
```

---

### Task 4: Wire grouping into Dispatch Board

**Files:**
- Modify: `pages_app/dispatch_board.py` (`_render_dispatch_action_card()` around line 715, and the lane-rendering loop around lines 929-957)

**Interfaces:**
- Consumes: `group_loads_by_booking` from `services.load_grouping_service`, called with `require_same_status=True`.
- No change to `render_dispatch_workspace`, `open_load_workspace_dialog`, `_get_selected_dispatch_load`.

- [ ] **Step 1: Import the grouping helper**

Add near the top of `pages_app/dispatch_board.py`:
```python
from services.load_grouping_service import group_loads_by_booking
```

- [ ] **Step 2: Add a group-aware card renderer**

`_render_dispatch_action_card(row, action_label, card_key_prefix)` currently renders one card with a "Work Load" button that sets `dispatch_board_selected_row_id` to `row["_row_id"]`. Add a new function alongside it in `pages_app/dispatch_board.py`:

```python
def _render_dispatch_action_group_card(group_row, action_label: str, card_key_prefix: str) -> None:
    """Render one card for a collapsed multi-container booking group.

    Shows the same summary info as _render_dispatch_action_card plus the
    "N containers" badge. Instead of opening the workspace directly, reveals
    a small picker so the dispatcher chooses which container to work.
    """
    row_ids = list(group_row.get("_grouped_row_ids", []))
    containers_label = group_row.get("Containers", f"{len(row_ids)} containers")

    _render_dispatch_action_card(group_row, action_label, card_key_prefix)
    st.caption(f"📦 {containers_label} — pick one below to work it")

    picker_key = f"dispatch_group_picker_{card_key_prefix}"
    if st.session_state.get(f"{picker_key}_open"):
        for row_id in row_ids:
            if st.button(f"Work container (load {row_id})", key=f"{picker_key}_{row_id}", use_container_width=True):
                st.session_state["dispatch_board_selected_row_id"] = row_id
                st.session_state.pop(f"{picker_key}_open", None)
                st.rerun()
    else:
        if st.button("Choose container to work", key=f"{picker_key}_toggle", use_container_width=True):
            st.session_state[f"{picker_key}_open"] = True
            st.rerun()
```

Note: `_render_dispatch_action_card` already renders its own "Work Load" button (from Task-independent existing code) that would set `dispatch_board_selected_row_id` directly to the *summary* row's `_row_id` — for a group card that summary row_id is just the first container's id, which is misleading since the picker below offers the real per-container choice. Change `_render_dispatch_action_card`'s existing "Work Load" button condition so it's skipped when called from the group path: add a `show_work_button: bool = True` parameter to `_render_dispatch_action_card`, default `True` (preserves current single-card behavior everywhere else it's called), and pass `show_work_button=False` from `_render_dispatch_action_group_card`. Wrap the existing button block:
```python
    if show_work_button and st.button("Work Load", key=f"dispatch_card_{card_key_prefix}_{row_id}", use_container_width=True):
        st.session_state["dispatch_board_selected_row_id"] = row_id
        st.rerun()
```

- [ ] **Step 3: Group before rendering cards in the lane loop**

Currently:
```python
                    if action_df.empty:
                        st.caption("No loads")
                    else:
                        for card_idx, (_, row) in enumerate(action_df.head(30).iterrows()):
                            _render_dispatch_action_card(row, action_label, f"{lane_name}_{action_key}_{card_idx}")
```
Replace with:
```python
                    if action_df.empty:
                        st.caption("No loads")
                    else:
                        grouped_action_df = group_loads_by_booking(action_df, require_same_status=True)
                        for card_idx, (_, row) in enumerate(grouped_action_df.head(30).iterrows()):
                            row_ids = row.get("_grouped_row_ids", [])
                            card_key = f"{lane_name}_{action_key}_{card_idx}"
                            if len(row_ids) > 1:
                                _render_dispatch_action_group_card(row, action_label, card_key)
                            else:
                                _render_dispatch_action_card(row, action_label, card_key)
```

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
```
Expected: exit 0.

Visually: open Dispatch Board. Single-container loads behave exactly as before. A multi-container booking where all containers share the same status shows one card with a "Choose container to work" picker; if you have one where statuses differ, confirm it still shows as separate individual cards (the safety behavior from brainstorming).

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Collapse same-status multi-container bookings into one card on Dispatch Board"
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
Expected: all prior tests plus the 7 new `load_grouping_service` tests pass (total = prior count + 7).

- [ ] **Step 3: Manual visual pass**

With `streamlit run app.py` running: check Orders/Load Management and Dispatch Board with both a single-container load and a multi-container booking (create one via the Operations Inbox "Create N Container Work Order(s)" flow if you don't already have one) to confirm the collapse/expand behavior end-to-end.
