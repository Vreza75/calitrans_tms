# Dispatch Board Lane Redesign Implementation Plan (Phase 4, part 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Dispatch Board's Verification/Planning/Execution/Completion lanes with columns driven by the Phase 3 operational stage model, and stop showing pre-dispatch (not-yet-Ready-to-Dispatch) loads on the active board at all — the core complaint in the original redesign request.

**Architecture:** A new pure module maps move-type-specific operational statuses onto the spec's 8 shared board stages for the "All Service Flows" view; when a single service flow is selected, the board shows that move type's exact stage sequence as columns instead. `render_dispatch_board_focused()` is rewritten to use this instead of `_dispatch_action_metadata()`'s lane-bucket logic (which stays in place, unused by the board, since other code may still reference it — confirmed not to be touched by this plan).

**Tech Stack:** Streamlit, pandas, `services.dispatch_stages` (Phase 3).

## Global Constraints

- Do not change `_dispatch_workflow_for_type`, `DISPATCH_ACTION_WORKFLOWS`, or `_dispatch_action_metadata` — leave the old lane system in the codebase, just stop using it for the active board's column layout. (Confirm before Phase 6 cleanup whether anything else still depends on it.)
- The multi-container grouping (`group_loads_by_booking`, `require_same_status=True`) must keep working exactly as verified this session — reuse it unchanged in the new column-rendering loop.
- The "Move Type" radio is removed as redundant with the existing "Service Flow" filter (which already supports "All" + the four flows) — this is a deliberate simplification, not an oversight.
- `render_dispatch_workspace`, `open_load_workspace_dialog`, and the Status Update tab wiring from the previous plan are untouched.
- Known, deliberate gap in this batch: "Completed Today" board scope and an "Active Drivers" metric are in the original spec's recommended list but need reliable completion timestamps this codebase doesn't track yet — not implemented here, flagged for a follow-up once `dispatch_transition_service` or a schema addition can supply that data honestly rather than approximating it.

---

### Task 1: Shared board stage mapping

**Files:**
- Create: `services/dispatch_board_view.py`
- Test: `tests/test_dispatch_board_view.py`

**Interfaces:**
- Produces: `SHARED_BOARD_STAGES: list[str]`, `to_shared_stage(move_type: str, status: str) -> str`, `get_board_columns(service_flow_filter: str) -> list[str]`, `is_active_dispatch_status(move_type: str, status: str) -> bool`. Task 2 imports all four.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_board_view.py`:
```python
from services.dispatch_board_view import (
    SHARED_BOARD_STAGES,
    get_board_columns,
    is_active_dispatch_status,
    to_shared_stage,
)


def test_shared_board_stages_exact_list():
    assert SHARED_BOARD_STAGES == [
        "Ready to Dispatch",
        "Assigned",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Empty Return",
        "Completed",
    ]


def test_to_shared_stage_import_en_route_and_at_port():
    assert to_shared_stage("Import", "En Route to Port") == "En Route to Pickup"
    assert to_shared_stage("Import", "At Port") == "At Pickup"
    assert to_shared_stage("Import", "Container Picked Up") == "At Pickup"
    assert to_shared_stage("Import", "Returning Empty") == "Empty Return"
    assert to_shared_stage("Import", "Dispatch Complete") == "Completed"


def test_to_shared_stage_export_in_gated_maps_to_completed():
    assert to_shared_stage("Export", "In-Gated") == "Completed"
    assert to_shared_stage("Export", "At Port") == "At Delivery"


def test_to_shared_stage_local_import_and_export_share_mapping():
    assert to_shared_stage("Local Import", "At Origin Warehouse") == "At Pickup"
    assert to_shared_stage("Local Export", "At Origin Warehouse") == "At Pickup"
    assert to_shared_stage("Local Import", "Delivered") == "At Delivery"


def test_to_shared_stage_unknown_status_returns_empty_string():
    assert to_shared_stage("Import", "Not A Real Status") == ""


def test_get_board_columns_all_returns_shared_stages():
    assert get_board_columns("All") == SHARED_BOARD_STAGES


def test_get_board_columns_specific_flow_returns_operational_stages():
    columns = get_board_columns("Export")
    assert columns[0] == "Ready to Dispatch"
    assert "In-Gated" in columns
    assert "Empty Return" not in columns


def test_is_active_dispatch_status_true_for_ready_to_dispatch_and_later():
    assert is_active_dispatch_status("Import", "Ready to Dispatch") is True
    assert is_active_dispatch_status("Import", "At Port") is True


def test_is_active_dispatch_status_false_for_pre_dispatch():
    assert is_active_dispatch_status("Import", "Booking Verified") is False
    assert is_active_dispatch_status("Import", "New") is False


def test_is_active_dispatch_status_false_for_dispatch_complete():
    assert is_active_dispatch_status("Import", "Dispatch Complete") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_board_view.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/dispatch_board_view.py`**

```python
from __future__ import annotations

from services.dispatch_stages import COMPLETION_STATUS, get_operational_stages
from services.workflow_constants import normalize_service_flow

SHARED_BOARD_STAGES = [
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "En Route to Delivery",
    "At Delivery",
    "Empty Return",
    "Completed",
]

_SHARED_STAGE_MAP: dict[str, dict[str, str]] = {
    "Import": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Port": "En Route to Pickup",
        "At Port": "At Pickup",
        "Container Picked Up": "At Pickup",
        "En Route to Delivery Warehouse": "En Route to Delivery",
        "At Delivery Warehouse": "At Delivery",
        "Delivered": "At Delivery",
        "Returning Empty": "Empty Return",
        "Empty Returned": "Empty Return",
        "Dispatch Complete": "Completed",
    },
    "Export": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Pickup Warehouse": "En Route to Pickup",
        "At Pickup Warehouse": "At Pickup",
        "Container Loaded": "At Pickup",
        "En Route to Port": "En Route to Delivery",
        "At Port": "At Delivery",
        "In-Gated": "Completed",
        "Dispatch Complete": "Completed",
    },
    "Local Import": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Origin Warehouse": "En Route to Pickup",
        "At Origin Warehouse": "At Pickup",
        "Loaded / Picked Up": "At Pickup",
        "En Route to Destination Warehouse": "En Route to Delivery",
        "At Destination Warehouse": "At Delivery",
        "Delivered": "At Delivery",
        "Dispatch Complete": "Completed",
    },
}
_SHARED_STAGE_MAP["Local Export"] = _SHARED_STAGE_MAP["Local Import"]


def to_shared_stage(move_type: str, status: str) -> str:
    """Map a move-type-specific operational status to one of the 8 shared
    board buckets, for the "All Service Flows" board view. Returns "" for
    a status this move type doesn't recognize."""
    normalized = normalize_service_flow(move_type, default="Local Import")
    mapping = _SHARED_STAGE_MAP.get(normalized, _SHARED_STAGE_MAP["Local Import"])
    return mapping.get(status, "")


def get_board_columns(service_flow_filter: str) -> list[str]:
    """Column set for the active board: the 8 shared buckets when viewing
    all service flows, or that move type's exact operational stage
    sequence when filtered to one specific flow."""
    if service_flow_filter == "All":
        return list(SHARED_BOARD_STAGES)
    return get_operational_stages(service_flow_filter)


def is_active_dispatch_status(move_type: str, status: str) -> bool:
    """True once a load has reached Ready to Dispatch and hasn't yet
    reached Dispatch Complete — i.e. belongs on the active Dispatch Board."""
    stages = get_operational_stages(move_type)
    return status in stages and status != COMPLETION_STATUS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_board_view.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/dispatch_board_view.py tests/test_dispatch_board_view.py
git commit -m "Add shared board stage mapping for the Dispatch Board redesign"
```

---

### Task 2: Rebuild the board's columns and metrics

**Files:**
- Modify: `pages_app/dispatch_board.py` (imports; `_render_dispatch_action_card`'s readiness-bar section; the body of `render_dispatch_board_focused` from the metrics computation through the lane-rendering loop)

**Interfaces:**
- Consumes: `services.dispatch_board_view.{SHARED_BOARD_STAGES, to_shared_stage, get_board_columns, is_active_dispatch_status}`.
- `_render_dispatch_action_card`/`_render_dispatch_action_group_card` keep their existing signatures (including the now-cosmetically-unused `action_label` param, kept to avoid touching every call site — flagged for Phase 6 cleanup) — only their internal rendering changes.

- [ ] **Step 1: Add the import**

```python
from services.dispatch_board_view import (
    get_board_columns,
    is_active_dispatch_status,
    to_shared_stage,
)
```

- [ ] **Step 2: Replace the readiness-bar section of `_render_dispatch_action_card`**

Find this block inside `_render_dispatch_action_card` (the div showing a percent-readiness bar and "Next Action"):
```python
    readiness = int(row.get("Readiness %", 0) or 0)
    next_action = _clean_display_value(row.get("Next Action", ""), action_label)
    exceptions = [item.strip() for item in _safe_str(row.get("Exceptions", "")).split(",") if item.strip()]
```
Replace with:
```python
    current_location = _clean_display_value(row.get("current_location", ""), "")
    eta = _clean_display_value(row.get("eta", ""), "")
    exceptions = [item.strip() for item in _safe_str(row.get("Exceptions", "")).split(",") if item.strip()]
```

Then find:
```python
            <div style="height:6px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin:7px 0 4px 0;">
                <div style="height:6px;width:{max(0, min(readiness, 100))}%;background:{border_color};"></div>
            </div>
            <div style="font-size:10px;color:#475569;">{readiness}% ready | {escape(next_action)}</div>
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>From:</b> {escape(pickup)}<br>
                <b>To:</b> {escape(delivery)}
            </div>
```
Replace with:
```python
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>From:</b> {escape(pickup)}<br>
                <b>To:</b> {escape(delivery)}
            </div>
```

And find:
```python
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>Driver:</b> {escape(driver)} | <b>Truck:</b> {escape(truck)}<br>
                <b>Need:</b> {escape(need_date)} | <b>LFD:</b> {escape(lfd)}
            </div>
            <div style="margin-top:5px;">{exception_html}</div>
```
Replace with:
```python
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>Driver:</b> {escape(driver)} | <b>Truck:</b> {escape(truck)}<br>
                <b>Need:</b> {escape(need_date)} | <b>LFD:</b> {escape(lfd)}
            </div>
            {f'<div style="font-size:10px;color:#475569;margin-top:6px;"><b>Location:</b> {escape(current_location)} | <b>ETA:</b> {escape(eta)}</div>' if current_location or eta else ''}
            <div style="margin-top:5px;">{exception_html}</div>
```

This removes the readiness percentage/next-action line (meaningless for loads that have already passed dispatch readiness checks by definition of being on this board) and adds a location/ETA line using the `current_location`/`eta` fields already populated by `_update_load_extra_fields` (existing functionality, unchanged) — directly matching the spec's card requirements (ETA, last operational update) using data that already exists.

- [ ] **Step 3: Replace the metrics-through-lane-loop body of `render_dispatch_board_focused`**

Find (starting right after the `LFD Parsed` column assignment, through the end of the lane-rendering `for` loop, right before `selected_row_id = st.session_state.get(...)`):
```python
    readiness_rows = []
    action_rows = []
    for _, row in board_df.iterrows():
        readiness = _load_readiness_details(row, include_documents=False)
        action = _dispatch_action_metadata(row, readiness)
        readiness_rows.append(readiness)
        action_rows.append(action)

    board_df["Readiness %"] = [int(item.get("score", 0)) for item in readiness_rows]
    board_df["Next Action"] = [item.get("next_action", "") for item in readiness_rows]
    board_df["Exceptions"] = [", ".join(item.get("exceptions", [])) for item in readiness_rows]
    board_df["Dispatch Lane"] = [item.get("lane", "") for item in action_rows]
    board_df["Dispatch Action"] = [item.get("action", "") for item in action_rows]
    board_df["Dispatch Action Label"] = [item.get("label", "") for item in action_rows]
    board_df["Dispatch Hint"] = [item.get("hint", "") for item in action_rows]
    board_df["Dispatch Lane Sort"] = [int(item.get("lane_sort", 0)) for item in action_rows]
    board_df["Dispatch Action Sort"] = [int(item.get("action_sort", 0)) for item in action_rows]
    board_df["Exception Count"] = board_df["Exceptions"].apply(lambda value: len([item for item in _safe_str(value).split(",") if item.strip()]))

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    controls = st.columns([1.3, 1.3, 1, 2.4])
    with controls[0]:
        selected_scope = st.radio(
            "Board Scope",
            ["All Active", "Due Today / Late", "Tomorrow", "Future Pipeline"],
            horizontal=False,
            key="dispatch_board_scope",
        )
    type_counts = board_df["Dispatch Move Type"].value_counts().to_dict()
    type_options = [move_type for move_type in DISPATCH_MOVE_TYPES if move_type == "Other" or type_counts.get(move_type, 0) > 0]
    if not type_options:
        type_options = DISPATCH_MOVE_TYPES
    with controls[1]:
        type_key = f"dispatch_board_move_type_{selected_scope}"
        if st.session_state.get(type_key) not in [None, *type_options]:
            st.session_state[type_key] = type_options[0]
        selected_type = st.radio(
            "Move Type",
            type_options,
            horizontal=False,
            key=type_key,
            format_func=lambda value: f"{value} ({type_counts.get(value, 0)})",
        )
    with controls[2]:
        exception_only = st.checkbox("Exceptions only", value=False, key="dispatch_board_exception_only")
    with controls[3]:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, load, container, customer, driver, truck, port, warehouse",
            key="dispatch_board_search",
        )

    scope_df = board_df[~board_df["Status"].isin(CLOSED_STATUSES)].copy()
    if selected_scope == "Due Today / Late":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().le(today)
        ].copy()
    elif selected_scope == "Tomorrow":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        ].copy()
    elif selected_scope == "Future Pipeline":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        ].copy()

    scope_df = scope_df[scope_df["Dispatch Move Type"].eq(selected_type)].copy()

    if exception_only:
        scope_df = scope_df[scope_df["Exception Count"].gt(0)].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "Booking Number",
            "Load ID",
            "Reference Number",
            "Container Number",
            "Customer",
            "Port",
            "Warehouse",
            "Address",
            "Driver Name",
            "Truck Assigned",
            "Chassis",
            "Status",
            "Dispatch Action Label",
            "Next Action",
            "Dispatcher Notes",
        ]
        available_columns = [column for column in searchable_columns if column in scope_df.columns]
        search_blob = scope_df[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            mask = search_blob.str.contains(re.escape(term), na=False)
            scope_df = scope_df[mask].copy()
            search_blob = search_blob[mask]

    metric_cols = st.columns(6)
    metric_cols[0].metric("Visible Loads", len(scope_df))
    metric_cols[1].metric("Verification", int(scope_df["Dispatch Lane"].eq("Verification").sum()))
    metric_cols[2].metric("Planning", int(scope_df["Dispatch Lane"].eq("Planning").sum()))
    metric_cols[3].metric("Execution", int(scope_df["Dispatch Lane"].eq("Execution").sum()))
    metric_cols[4].metric("Exceptions", int(scope_df["Exception Count"].gt(0).sum()))
    metric_cols[5].metric("Billing Ready", int(scope_df["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum()))

    exception_counts = _load_exception_summary(scope_df)
    exception_labels = ["Late appointment", "No PIN", "Customer waiting", "Waiting driver", "Port hold", "Ready for billing"]
    exception_cols = st.columns(len(exception_labels))
    for idx, label in enumerate(exception_labels):
        exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))

    if scope_df.empty:
        st.info(f"No {selected_type} loads match the current Dispatch Board filters.")
    else:
        workflow = _dispatch_workflow_for_type(selected_type)
        sorted_df = scope_df.sort_values(
            ["Dispatch Lane Sort", "Dispatch Action Sort", "Exception Count", "Delivery Date Parsed", "LFD Parsed", "_row_id"],
            ascending=[True, True, False, True, True, True],
            na_position="last",
        )
        for lane_name, actions in workflow.items():
            lane_df = sorted_df[sorted_df["Dispatch Lane"].eq(lane_name)].copy()
            st.markdown(f"### {lane_name}")
            lane_cols = st.columns(len(actions), gap="small")
            for idx, (action_key, action_label) in enumerate(actions):
                with lane_cols[idx]:
                    action_df = lane_df[lane_df["Dispatch Action"].eq(action_key)].copy()
                    st.markdown(
                        f"""
                        <div style="
                            background:#f8fafc;
                            border:1px solid #cbd5e1;
                            border-radius:8px;
                            padding:8px;
                            margin-bottom:8px;
                            text-align:center;
                            min-height:58px;
                        ">
                            <div style="font-size:12px;font-weight:800;color:#0f172a;">{escape(action_label)}</div>
                            <div style="font-size:20px;font-weight:900;color:#0f172a;">{len(action_df)}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
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

Replace the entire block above with:
```python
    readiness_rows = [_load_readiness_details(row, include_documents=False) for _, row in board_df.iterrows()]
    board_df["Exceptions"] = [", ".join(item.get("exceptions", [])) for item in readiness_rows]
    board_df["Exception Count"] = board_df["Exceptions"].apply(lambda value: len([item for item in _safe_str(value).split(",") if item.strip()]))
    board_df["Board Stage"] = [
        to_shared_stage(row["Dispatch Move Type"], row["Status"]) if selected_flow == "All" else row["Status"]
        for _, row in board_df.iterrows()
    ]
    board_df["Is Active Dispatch"] = [
        is_active_dispatch_status(row["Dispatch Move Type"], row["Status"]) for _, row in board_df.iterrows()
    ]

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    controls = st.columns([1.3, 1, 2.4])
    with controls[0]:
        selected_scope = st.radio(
            "Board Scope",
            ["Active Now", "Due Today / Late", "Tomorrow", "Future Pipeline"],
            horizontal=False,
            key="dispatch_board_scope",
        )
    with controls[1]:
        exception_only = st.checkbox("Exceptions only", value=False, key="dispatch_board_exception_only")
    with controls[2]:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, load, container, customer, driver, truck, port, warehouse",
            key="dispatch_board_search",
        )

    scope_df = board_df[board_df["Is Active Dispatch"]].copy()
    if selected_scope == "Due Today / Late":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().le(today)
        ].copy()
    elif selected_scope == "Tomorrow":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        ].copy()
    elif selected_scope == "Future Pipeline":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        ].copy()

    if exception_only:
        scope_df = scope_df[scope_df["Exception Count"].gt(0)].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "Booking Number",
            "Load ID",
            "Reference Number",
            "Container Number",
            "Customer",
            "Port",
            "Warehouse",
            "Address",
            "Driver Name",
            "Truck Assigned",
            "Chassis",
            "Status",
            "Dispatcher Notes",
        ]
        available_columns = [column for column in searchable_columns if column in scope_df.columns]
        search_blob = scope_df[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            mask = search_blob.str.contains(re.escape(term), na=False)
            scope_df = scope_df[mask].copy()
            search_blob = search_blob[mask]

    board_stage_column = "Board Stage" if selected_flow == "All" else "Status"
    unassigned_mask = scope_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])

    metric_cols = st.columns(6)
    metric_cols[0].metric("Ready to Dispatch", int(scope_df[board_stage_column].eq("Ready to Dispatch").sum()))
    metric_cols[1].metric("Unassigned", int(unassigned_mask.sum()))
    metric_cols[2].metric(
        "En Route",
        int(scope_df[board_stage_column].isin(["En Route to Pickup", "En Route to Delivery"]).sum())
        if selected_flow == "All"
        else int(scope_df["Status"].astype(str).str.startswith("En Route").sum()),
    )
    metric_cols[3].metric(
        "At Pickup",
        int(scope_df[board_stage_column].eq("At Pickup").sum())
        if selected_flow == "All"
        else int(scope_df["Status"].astype(str).str.contains("At Pickup Warehouse|At Port|At Origin Warehouse", regex=True, na=False).sum()),
    )
    metric_cols[4].metric(
        "At Delivery",
        int(scope_df[board_stage_column].eq("At Delivery").sum())
        if selected_flow == "All"
        else int(scope_df["Status"].astype(str).str.contains("At Delivery Warehouse|At Destination Warehouse", regex=True, na=False).sum()),
    )
    metric_cols[5].metric("Active Exceptions", int(scope_df["Exception Count"].gt(0).sum()))

    exception_counts = _load_exception_summary(scope_df)
    exception_labels = ["Late appointment", "No PIN", "Waiting driver", "Port hold"]
    exception_cols = st.columns(len(exception_labels))
    for idx, label in enumerate(exception_labels):
        exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))

    if scope_df.empty:
        st.info("No active dispatch loads match the current Dispatch Board filters.")
    else:
        columns = get_board_columns(selected_flow)
        stage_sort = {stage: idx for idx, stage in enumerate(columns)}
        scope_df["Board Stage Sort"] = scope_df[board_stage_column].map(stage_sort).fillna(len(columns))
        sorted_df = scope_df.sort_values(
            ["Board Stage Sort", "Exception Count", "Delivery Date Parsed", "LFD Parsed", "_row_id"],
            ascending=[True, False, True, True, True],
            na_position="last",
        )
        board_cols = st.columns(len(columns), gap="small")
        for idx, stage_name in enumerate(columns):
            with board_cols[idx]:
                stage_df = sorted_df[sorted_df[board_stage_column].eq(stage_name)].copy()
                st.markdown(
                    f"""
                    <div style="
                        background:#f8fafc;
                        border:1px solid #cbd5e1;
                        border-radius:8px;
                        padding:8px;
                        margin-bottom:8px;
                        text-align:center;
                        min-height:58px;
                    ">
                        <div style="font-size:12px;font-weight:800;color:#0f172a;">{escape(stage_name)}</div>
                        <div style="font-size:20px;font-weight:900;color:#0f172a;">{len(stage_df)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if stage_df.empty:
                    st.caption("No loads")
                else:
                    grouped_stage_df = group_loads_by_booking(stage_df, require_same_status=True)
                    for card_idx, (_, row) in enumerate(grouped_stage_df.head(30).iterrows()):
                        row_ids = row.get("_grouped_row_ids", [])
                        card_key = f"{stage_name}_{card_idx}"
                        if len(row_ids) > 1:
                            _render_dispatch_action_group_card(row, stage_name, card_key)
                        else:
                            _render_dispatch_action_card(row, stage_name, card_key)
```

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
```
Expected: exit 0.

Visually, with the running app:
  - Dispatch Board no longer shows "New Orders"/"Needs Verification"/"Documents"/"Sync Port Data"/"Send Packet"/"Ready for Billing" columns — only operational movement columns.
  - A load still in `New`/`Hold/Need Info`/etc. no longer appears anywhere on the active board (it's filtered out by `Is Active Dispatch`).
  - Selecting "All" for Service Flow shows the 8 shared columns (Ready to Dispatch, Assigned, En Route to Pickup, At Pickup, En Route to Delivery, At Delivery, Empty Return, Completed) mixing all move types.
  - Selecting a specific flow (e.g. "Export") shows that flow's exact stage columns (Ready to Dispatch, Driver Assigned, En Route to Pickup Warehouse, At Pickup Warehouse, Container Loaded, En Route to Port, At Port, In-Gated, Dispatch Complete).
  - The RICGX1235800 multi-container booking still shows as one collapsed card with the "N containers" badge and picker (this session's earlier grouping work) — confirm this explicitly, it's easy to break with a column-loop rewrite this size.
  - Cards show a Location/ETA line when that data exists, no readiness percentage bar.

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Replace Dispatch Board lanes with operational-stage columns, hide pre-dispatch loads from the active board"
```

---

### Task 3: Full verification

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all 63 prior tests plus this plan's 10 new tests pass (73 total).
