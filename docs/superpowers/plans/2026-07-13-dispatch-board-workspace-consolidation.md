# Dispatch Board Filter/KPI Cleanup & Booking Workspace Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed Dispatch Board UX problems (filter order, KPI dominance, incomplete status-color coverage, Port/PIN shown for Local moves, booking cards opening a thinner duplicate workspace in a new tab with broken back-navigation) without rewriting working code — by extracting/reusing what already exists (`render_dispatch_workspace`, `dispatch_stages`, `dispatch_card_view_model`, `STATUS_COLORS`) rather than building a second implementation.

**Architecture:** `render_dispatch_workspace(selected_load, ...)` in `pages_app/dispatch_board.py` already contains the full dispatcher control set (status, driver/truck/chassis, driver communication, notes, billing) and is kept exactly as-is for its existing caller (`pages_app/active_status.py`). A new `render_booking_workspace(booking_df, ...)` wraps it to add a compact booking-level header and per-container tabs for multi-container bookings, reusing `render_dispatch_workspace` per container instead of re-implementing its tabs. Booking cards are repointed from an `<a href target="_blank">` (which opens `pages_app/booking_detail.py` in a new tab, outside the app shell) to a same-tab Streamlit button that sets session state — this is what actually fixes the "lost controls" and "back navigation" complaints, since Streamlit's keyed widgets already persist filter selections in `st.session_state` across reruns for free. `pages_app/booking_detail.py` and its query-param route are retired once nothing points at them (confirmed zero test references).

**Tech Stack:** Streamlit, pandas. No new dependencies, no schema changes.

## Global Constraints

- Do not change `render_dispatch_workspace(selected_load, refresh_callback=None, port_houston_panel_renderer=None)`'s signature — `pages_app/active_status.py:194` calls it directly with a single-row `pd.Series` and must keep working unchanged.
- Do not touch `services/dispatch_stages.py`, `services/dispatch_transition_service.py`, `services/dispatch_card_view_model.py`, `services/load_grouping_service.py`, or `services/dispatch_board_view.py` — all already correct per this session's audit.
- `STATUS_COLORS`, `STATUS_MEANINGS`, `_get_status_color`, `_get_status_border_color` in `services/dispatch_workflow_service.py` keep their exact existing names/signatures — other files already import them (`ui_components/status_badge.py`, `ui_components/status_legend.py`). Extend by consolidating into one new `STATUS_UI` source, not by hand-editing two dicts in parallel.
- No new database tables or columns. "Operational Notes" reuses the existing `dispatch_messages` table (already supports arbitrary `message_type` values, already has `created_at`/`sent_by`) with a new `message_type="operational_note"` value — confirmed zero schema impact.
- `pages_app/booking_detail.py` is retired only after confirming (already done this session — `grep -rn "booking_detail\|render_booking_detail"` returns only `pages_app/router.py`, docs, and itself; no test references) that nothing else depends on it.

**Known, deliberate gaps in this batch** (flagged rather than silently skipped):
- The user's spec's "recommended" 5-tab regroup (Dispatch / Communication / Notes / Documents & Billing / Port-PIN) is not done here. `render_dispatch_workspace` already uses 8-9 clearly separated tabs, not "one long unstructured form" — the complaint this addresses. Regrouping is a larger, higher-risk rewrite of a 550-line function with a second live caller (`active_status.py`); scope it as a separate follow-up plan if still wanted after this batch ships.
- Section 10 of the spec (responsive/accessible CSS layer — min touch targets, no horizontal overflow, keyboard focus states) is not addressed. Nothing in this batch's audit found a concrete bug in that area (no user complaint mapped to a confirmed source-level issue), and speculative CSS changes risk fighting Streamlit's layout engine per this repo's own constraint. Flag for a dedicated pass with actual browser testing, not paper changes.
- `_render_booking_card` stays a private function inside `pages_app/dispatch_board.py` rather than being extracted to `ui_components/` — matches this file's existing convention (all its other render helpers are private and file-local); extracting it is pure churn with no behavior change, skipped per CLAUDE.md's "do not split files only to reduce line count."

---

### Task 1: Consolidate status color/border into one `STATUS_UI` source, fill the missing canonical statuses

**Files:**
- Modify: `services/dispatch_workflow_service.py:202-232` (`STATUS_COLORS`), `:727-762` (`_get_status_color`, `_get_status_border_color`)
- Test: `tests/test_status_ui.py` (new)

**Interfaces:**
- Produces: `STATUS_UI: dict[str, dict[str, str]]` (keys: `background`, `border`, `text`), `get_status_ui(status: str) -> dict[str, str]` (safe neutral default for unknown status). `STATUS_COLORS` stays a `dict[str, str]` derived from `STATUS_UI` (unchanged shape/behavior for existing callers).
- Consumes: nothing new.

**Bug being fixed:** `STATUS_COLORS` and the border-color dict inside `_get_status_border_color` are two independently hand-maintained literals. Both are missing the 3 of 7 canonical dispatch stages from `services/dispatch_stages.SHARED_STAGES` that the redesigned board actually uses: `"En Route to Delivery"` (only the legacy-cased `"En Route To Delivery"` exists), `"At Delivery"`, and `"Completed"` (only `"Closed"` exists). Any load in one of those three statuses currently falls back to the generic default color everywhere `STATUS_COLORS`/`render_status_badge` is used — this is complaint #5 ("status colors inconsistent or missing").

- [ ] **Step 1: Write the failing test**

Create `tests/test_status_ui.py`:
```python
from services.dispatch_stages import SHARED_STAGES
from services.dispatch_workflow_service import STATUS_COLORS, get_status_ui


def test_every_shared_stage_has_a_status_color():
    for stage in SHARED_STAGES:
        assert stage in STATUS_COLORS, f"{stage!r} missing from STATUS_COLORS"


def test_get_status_ui_returns_background_border_text_for_known_status():
    ui = get_status_ui("Ready to Dispatch")
    assert ui["background"]
    assert ui["border"]
    assert ui["text"]


def test_get_status_ui_returns_safe_default_for_unknown_status():
    ui = get_status_ui("Not A Real Status")
    assert ui["background"]
    assert ui["border"]
    assert ui["text"]


def test_get_status_ui_matches_legacy_status_colors_dict():
    assert get_status_ui("Ready to Dispatch")["background"] == STATUS_COLORS["Ready to Dispatch"]


def test_completed_and_at_delivery_and_en_route_to_delivery_have_distinct_colors():
    completed = get_status_ui("Completed")["background"]
    at_delivery = get_status_ui("At Delivery")["background"]
    en_route = get_status_ui("En Route to Delivery")["background"]
    assert len({completed, at_delivery, en_route}) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_status_ui.py -v`
Expected: FAIL — `test_every_shared_stage_has_a_status_color` fails on `"En Route to Delivery"`; `ImportError: cannot import name 'get_status_ui'`.

- [ ] **Step 3: Replace `STATUS_COLORS` (L202-232) and the two color functions (L727-762) in `services/dispatch_workflow_service.py`**

Replace the existing `STATUS_COLORS = { ... }` block (current lines 202-232) with:
```python
STATUS_UI: dict[str, dict[str, str]] = {
    "New Email":                   {"background": "#f8fafc", "border": "#94a3b8", "text": "#0f172a"},
    "Needs Review":                {"background": "#fef3c7", "border": "#d97706", "text": "#0f172a"},
    "Order Created":                {"background": "#e0f2fe", "border": "#0284c7", "text": "#0f172a"},
    "New":                          {"background": "#f8fafc", "border": "#94a3b8", "text": "#0f172a"},
    "Hold/Need Info":               {"background": "#fecaca", "border": "#dc2626", "text": "#0f172a"},
    "Booking Verified":             {"background": "#dbeafe", "border": "#2563eb", "text": "#0f172a"},
    "Port Verified":                {"background": "#c7d2fe", "border": "#4f46e5", "text": "#0f172a"},
    "Ready for Appointment / PIN":  {"background": "#ddd6fe", "border": "#7c3aed", "text": "#0f172a"},
    "Ready for Port PIN":           {"background": "#ddd6fe", "border": "#7c3aed", "text": "#0f172a"},
    "PIN Received":                 {"background": "#bfdbfe", "border": "#1d4ed8", "text": "#0f172a"},
    "Awaiting Appointment":         {"background": "#fdba74", "border": "#ea580c", "text": "#0f172a"},
    "Ready to Dispatch":            {"background": "#bbf7d0", "border": "#16a34a", "text": "#0f172a"},
    "Driver Assigned":              {"background": "#dcfce7", "border": "#22c55e", "text": "#0f172a"},
    "Assigned":                     {"background": "#dcfce7", "border": "#22c55e", "text": "#0f172a"},
    "Dispatched":                   {"background": "#ccfbf1", "border": "#14b8a6", "text": "#0f172a"},
    "En Route to Pickup":           {"background": "#bef264", "border": "#65a30d", "text": "#0f172a"},
    "At Port":                      {"background": "#fde68a", "border": "#ca8a04", "text": "#0f172a"},
    "At Pickup":                    {"background": "#fde047", "border": "#ca8a04", "text": "#0f172a"},
    "Loaded / Picked Up":           {"background": "#a5b4fc", "border": "#4f46e5", "text": "#0f172a"},
    "Loaded":                       {"background": "#a5b4fc", "border": "#4f46e5", "text": "#0f172a"},
    "En Route To Delivery":         {"background": "#5eead4", "border": "#0d9488", "text": "#0f172a"},
    "En Route to Delivery":         {"background": "#5eead4", "border": "#0d9488", "text": "#0f172a"},
    "At Delivery":                  {"background": "#7dd3fc", "border": "#0284c7", "text": "#0f172a"},
    "Delivered":                    {"background": "#93c5fd", "border": "#2563eb", "text": "#0f172a"},
    "Returning Empty":              {"background": "#e0f2fe", "border": "#0284c7", "text": "#0f172a"},
    "Completed":                    {"background": "#86efac", "border": "#15803d", "text": "#0f172a"},
    "POD Received":                 {"background": "#60a5fa", "border": "#1d4ed8", "text": "#0f172a"},
    "Ready for ProfitTools":        {"background": "#4ade80", "border": "#15803d", "text": "#0f172a"},
    "Exported to ProfitTools":      {"background": "#c4b5fd", "border": "#7c3aed", "text": "#0f172a"},
    "Invoiced":                     {"background": "#f0abfc", "border": "#c026d3", "text": "#0f172a"},
    "Closed":                       {"background": "#d1d5db", "border": "#64748b", "text": "#0f172a"},
    "Cancelled":                    {"background": "#f87171", "border": "#b91c1c", "text": "#0f172a"},
}

_DEFAULT_STATUS_UI = {"background": "#f1f5f9", "border": "#94a3b8", "text": "#0f172a"}


def get_status_ui(status: str) -> dict[str, str]:
    """Single canonical lookup for a status's background/border/text colors.

    Returns a safe neutral default for any status not in STATUS_UI rather
    than raising — callers (badges, cards, lane headers, status history)
    never need their own fallback color.
    """
    return STATUS_UI.get(str(status or "").strip(), _DEFAULT_STATUS_UI)


# Derived view kept for existing callers (ui_components/status_badge.py,
# ui_components/status_legend.py, _status_row_style below) — STATUS_UI is
# the single source of truth now, this dict is not hand-maintained.
STATUS_COLORS = {status: ui["background"] for status, ui in STATUS_UI.items()}
```

Then replace `_get_status_color`/`_get_status_border_color` (current lines 727-762) with:
```python
def _get_status_color(status: str) -> str:
    return get_status_ui(status)["background"]

def _get_status_border_color(status: str) -> str:
    return get_status_ui(status)["border"]
```

`STATUS_MEANINGS` (lines 234-264) is unchanged — it's a separate, already-canonical concern.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_status_ui.py -v`
Expected: all 5 pass.

- [ ] **Step 5: Full regression check**

Run:
```powershell
python -m compileall -q services/dispatch_workflow_service.py ui_components/status_badge.py ui_components/status_legend.py
python -m pytest -q
```
Expected: compile exit 0; all prior tests (101) plus 5 new pass (106).

- [ ] **Step 6: Commit**

```bash
git add services/dispatch_workflow_service.py tests/test_status_ui.py
git commit -m "Consolidate status colors into one STATUS_UI source, add the 3 missing canonical dispatch stages"
```

---

### Task 2: Public `requires_port_pin()` helper

**Files:**
- Modify: `services/workflow_constants.py` (add function)
- Modify: `services/dispatch_workflow_service.py:319-320` (`_load_requires_port_type` delegates instead of duplicating)
- Test: `tests/test_workflow_constants.py` (extend if it exists, else create)

**Interfaces:**
- Produces: `requires_port_pin(service_flow: str) -> bool` in `services/workflow_constants.py`, importable with zero Streamlit/DB dependencies (matches `normalize_service_flow` already there).
- Consumes: `normalize_service_flow` (already in the same file).

- [ ] **Step 1: Write the failing test**

Check whether `tests/test_workflow_constants.py` exists first (`Glob tests/test_workflow_constants.py`). If it exists, add these functions to it; if not, create it with just these functions plus the necessary import line at top (`from services.workflow_constants import normalize_service_flow, requires_port_pin`).

```python
def test_import_requires_port_pin():
    assert requires_port_pin("Import") is True


def test_export_requires_port_pin():
    assert requires_port_pin("Export") is True


def test_local_import_does_not_require_port_pin():
    assert requires_port_pin("Local Import") is False


def test_local_export_does_not_require_port_pin():
    assert requires_port_pin("Local Export") is False


def test_requires_port_pin_normalizes_legacy_values_first():
    assert requires_port_pin("import") is True
    assert requires_port_pin("drayage import") is True
    assert requires_port_pin("local import move") is False


def test_requires_port_pin_unknown_value_is_false():
    assert requires_port_pin("") is False
    assert requires_port_pin("Something Else") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_workflow_constants.py -v`
Expected: FAIL — `ImportError: cannot import name 'requires_port_pin'`.

- [ ] **Step 3: Add `requires_port_pin()` to `services/workflow_constants.py`**

Add at the end of the file, after `service_flow_label`:
```python
def requires_port_pin(service_flow: str) -> bool:
    """True only for the two flows that touch a marine terminal (Import,
    Export). Local Import/Local Export never require Port Sync, a
    terminal PIN, or a port appointment — they may still have a warehouse
    or customer appointment, which is a different concept entirely."""
    return normalize_service_flow(service_flow, default="") in {"Import", "Export"}
```

- [ ] **Step 4: Delegate `_load_requires_port_type` instead of duplicating the check**

In `services/dispatch_workflow_service.py`, replace lines 319-320:
```python
def _load_requires_port_type(move_type: str) -> bool:
    return _normalize_load_type_value(move_type) in {"Import", "Export"}
```
with:
```python
def _load_requires_port_type(move_type: str) -> bool:
    return requires_port_pin(move_type)
```
Add the import near the top of `services/dispatch_workflow_service.py` (alongside any existing `services.workflow_constants` import — check first with `grep -n "^from services.workflow_constants" services/dispatch_workflow_service.py`; if already imported, just add `requires_port_pin` to that import line, else add a new line):
```python
from services.workflow_constants import requires_port_pin
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_workflow_constants.py -v`
Expected: all 6 pass.

- [ ] **Step 6: Full regression check**

Run:
```powershell
python -m compileall -q services/workflow_constants.py services/dispatch_workflow_service.py
python -m pytest -q
```
Expected: compile exit 0; 106 + 6 = 112 tests pass. (`_normalize_load_type_value` may now be unused in `dispatch_workflow_service.py` if `_load_requires_port_type` was its only caller for this check — grep before assuming: `grep -n "_normalize_load_type_value" services/dispatch_workflow_service.py`. If it's still used elsewhere in the file, leave it; do not remove a function still called elsewhere just because this one call site changed.)

- [ ] **Step 7: Commit**

```bash
git add services/workflow_constants.py services/dispatch_workflow_service.py tests/test_workflow_constants.py
git commit -m "Add public requires_port_pin() helper, delegate the existing private check to it"
```

---

### Task 3: Dispatch Board filter reorder + collapsible Operational Summary

**Files:**
- Modify: `pages_app/dispatch_board.py:617-793` (`render_dispatch_board_focused`)

**Interfaces:** none new — pure reordering/wrapping of existing widgets and metrics.

- [ ] **Step 1: Move the Status Filter selectbox to directly under Service Flow**

Current code at lines 628-630:
```python
    selected_flow = render_service_flow_filter("dispatch_board_service_flow")
    board_df = apply_service_flow_filter(board_df, selected_flow)
    board_df["Dispatch Move Type"] = board_df["TYPE"].apply(_normalize_load_type_value)
```
Replace with (adds the Status Filter widget declaration here — its filtering application stays where it already is, later in the function, since `scope_df` doesn't exist yet at this point):
```python
    selected_flow = render_service_flow_filter("dispatch_board_service_flow")
    board_df = apply_service_flow_filter(board_df, selected_flow)
    board_df["Dispatch Move Type"] = board_df["TYPE"].apply(_normalize_load_type_value)

    status_filter = st.selectbox(
        "Status Filter",
        ["All Active"] + get_board_columns(),
        key="dispatch_board_status_filter",
    )
```

Then find the old declaration site (currently lines 739-745):
```python
    status_filter = st.selectbox(
        "Status Filter",
        ["All Active"] + get_board_columns(),
        key="dispatch_board_status_filter",
    )
    if status_filter != "All Active":
        scope_df = scope_df[scope_df["Status"].eq(status_filter)].copy()
```
Replace with just the filtering application (the widget declaration was moved up in this step, so re-declaring it here with the same `key` would raise a `StreamlitDuplicateElementKey` error):
```python
    if status_filter != "All Active":
        scope_df = scope_df[scope_df["Status"].eq(status_filter)].copy()
```

- [ ] **Step 2: Wrap the three KPI blocks in a collapsible "Operational Summary" expander**

Current code (lines 775-793):
```python
    summary_cols = st.columns(4)
    summary_cols[0].metric("Bookings", len(booking_identities))
    summary_cols[1].metric("Containers", len(scope_df))
    summary_cols[2].metric("Unassigned", int(unassigned_mask.sum()))
    summary_cols[3].metric("Active Exceptions", int(scope_df["Exception Count"].gt(0).sum()))

    metric_cols = st.columns(6)
    metric_cols[0].metric("Ready to Dispatch", int(scope_df["Status"].eq("Ready to Dispatch").sum()))
    metric_cols[1].metric("En Route", int(scope_df["Status"].isin(["En Route to Pickup", "En Route to Delivery"]).sum()))
    metric_cols[2].metric("At Pickup", int(scope_df["Status"].eq("At Pickup").sum()))
    metric_cols[3].metric("At Delivery", int(scope_df["Status"].eq("At Delivery").sum()))
    metric_cols[4].metric("Empty Returns Due", int(scope_df["Status"].eq("Returning Empty").sum()))
    metric_cols[5].metric("Completed Today", len(completed_df))

    exception_counts = _load_exception_summary(scope_df)
    exception_labels = ["Late appointment", "No PIN", "Waiting driver", "Port hold"]
    exception_cols = st.columns(len(exception_labels))
    for idx, label in enumerate(exception_labels):
        exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))
```
Replace with:
```python
    active_exception_count = int(scope_df["Exception Count"].gt(0).sum())

    with st.expander("Operational Summary", expanded=active_exception_count > 0):
        summary_cols = st.columns(4)
        summary_cols[0].metric("Bookings", len(booking_identities))
        summary_cols[1].metric("Containers", len(scope_df))
        summary_cols[2].metric("Unassigned", int(unassigned_mask.sum()))
        summary_cols[3].metric("Active Exceptions", active_exception_count)

        metric_cols = st.columns(6)
        metric_cols[0].metric("Ready to Dispatch", int(scope_df["Status"].eq("Ready to Dispatch").sum()))
        metric_cols[1].metric("En Route", int(scope_df["Status"].isin(["En Route to Pickup", "En Route to Delivery"]).sum()))
        metric_cols[2].metric("At Pickup", int(scope_df["Status"].eq("At Pickup").sum()))
        metric_cols[3].metric("At Delivery", int(scope_df["Status"].eq("At Delivery").sum()))
        metric_cols[4].metric("Empty Returns Due", int(scope_df["Status"].eq("Returning Empty").sum()))
        metric_cols[5].metric("Completed Today", len(completed_df))

        exception_counts = _load_exception_summary(scope_df)
        exception_labels = ["Late appointment", "No PIN", "Waiting driver", "Port hold"]
        exception_cols = st.columns(len(exception_labels))
        for idx, label in enumerate(exception_labels):
            exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))
```

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
python -m pytest -q
```
Expected: compile exit 0; 112 tests still pass (this step is UI-only, no new tests).

Visually, with the running app: Dispatch Board shows Service Flow, then Status Filter, directly below each other, above Board Scope/Exceptions/Search/Customer/Driver/Port/Warehouse. "Operational Summary" is collapsed by default when there are zero active exceptions in the current filtered view, expanded automatically when there's at least one.

- [ ] **Step 4: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Move Status Filter directly under Service Flow, collapse KPIs into an Operational Summary expander"
```

---

### Task 4: Gate Port/PIN tab by service flow, add Operational Notes

**Files:**
- Modify: `pages_app/dispatch_board.py:189-218` (`render_dispatch_workspace` — tab list), `:501-514` (`customer_tab` body, to add the notes panel above it or as its own tab)

**Interfaces:**
- Consumes: `requires_port_pin` from `services.workflow_constants` (Task 2), `_insert_dispatch_message`/`_read_dispatch_messages` from `services.dispatch_data_service` (already imported).

- [ ] **Step 1: Add the import**

In `pages_app/dispatch_board.py`, add to the existing import block near the top:
```python
from services.workflow_constants import requires_port_pin
```

- [ ] **Step 2: Make the Port Sync/PIN tab conditional**

Current code (lines 216-218):
```python
    dispatch_tab, port_tab, status_tab, timeline_tab, driver_tab, customer_tab, docs_tab, billing_tab = st.tabs(
        ["Dispatch Details", "Port Sync / PIN", "Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Documents", "Billing"]
    )
```
Replace with:
```python
    move_type_for_tabs = _normalize_load_type(selected_load)
    show_port_tab = requires_port_pin(move_type_for_tabs)

    tab_labels = ["Dispatch Details"]
    if show_port_tab:
        tab_labels.append("Port Sync / PIN")
    tab_labels += ["Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Notes", "Documents", "Billing"]
    tabs = st.tabs(tab_labels)
    tab_iter = iter(tabs)
    dispatch_tab = next(tab_iter)
    port_tab = next(tab_iter) if show_port_tab else None
    status_tab = next(tab_iter)
    timeline_tab = next(tab_iter)
    driver_tab = next(tab_iter)
    customer_tab = next(tab_iter)
    notes_tab = next(tab_iter)
    docs_tab = next(tab_iter)
    billing_tab = next(tab_iter)
```

Then find:
```python
    with port_tab:
        _render_port_panel(selected_load, readiness, port_houston_panel_renderer)
```
Replace with:
```python
    if port_tab is not None:
        with port_tab:
            _render_port_panel(selected_load, readiness, port_houston_panel_renderer)
```

- [ ] **Step 3: Add an Operational Notes panel in the new `notes_tab`**

Add this new block right after the existing `with customer_tab:` block (after line 514, before `with docs_tab:`):
```python
    with notes_tab:
        st.markdown("### Operational Notes")
        st.caption("Internal operations notes, separate from customer-facing communication and from the Dispatcher status note.")
        operational_note = st.text_area(
            "Add Operational Note",
            placeholder="Example: Chassis swapped at yard before dispatch, confirmed with yard checker.",
            height=100,
            key=f"operational_note_{load_id}",
        )
        if st.button("Save Operational Note", key=f"save_operational_note_{load_id}"):
            if not operational_note.strip():
                st.error("Note is required.")
            else:
                _insert_dispatch_message(load_id, "operational_note", "internal", "dispatcher", operational_note.strip())
                st.success("Operational note saved.")
                st.rerun()

        messages = _read_dispatch_messages(load_id)
        operational_notes = messages[
            messages["message_type"].astype(str).eq("operational_note")
        ] if not messages.empty else pd.DataFrame()
        if operational_notes.empty:
            st.info("No operational notes yet.")
        else:
            display_cols = [c for c in ["created_at", "sent_by", "message_body"] if c in operational_notes.columns]
            st.dataframe(operational_notes[display_cols], use_container_width=True, hide_index=True)

        st.markdown("### Dispatcher Notes")
        st.caption("Shown on Status Update — editable there, displayed here for quick reference.")
        st.info(str(selected_load.get("Dispatcher Notes", "") or "No dispatcher notes yet."))
```

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
python -m pytest -q
```
Expected: compile exit 0; 112 tests pass.

Visually: open an Import or Export load's workspace — "Port Sync / PIN" tab is present. Open a Local Import or Local Export load's workspace — "Port Sync / PIN" does not appear at all (not just hidden/empty). Confirm the new "Notes" tab shows Operational Notes (saveable, listed with timestamp/author) and a read-only Dispatcher Notes reference. Confirm `active_status.py`'s workspace (same function) shows the same conditional Port tab behavior.

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Gate Port Sync/PIN tab to Import/Export only, add an Operational Notes panel"
```

---

### Task 5: Booking-level workspace wrapper, repoint booking cards to open it in-app

**Files:**
- Modify: `pages_app/dispatch_board.py` (`_render_booking_card`, new `render_booking_workspace`, the selection-handling block at the end of `render_dispatch_board_focused`, currently lines 800-820)

**Interfaces:**
- Produces: `render_booking_workspace(booking_df: pd.DataFrame, refresh_callback=None, port_houston_panel_renderer=None) -> None` — booking-level wrapper; for a single-container booking it renders `render_dispatch_workspace` directly (no extra tab wrapper); for multiple containers it shows a compact header + a "Booking Summary" tab + one tab per container, each calling `render_dispatch_workspace` unchanged.
- Consumes: `render_dispatch_workspace` (unchanged, Task 1-4's version), `get_status_ui` (Task 1), `requires_port_pin` (Task 2).

- [ ] **Step 1: Repoint `_render_booking_card` from a new-tab link to an in-app button**

Current code (lines 554-567):
```python
def _render_booking_card(card: dict) -> None:
    display_status = get_display_label(card["move_type"], card["canonical_status"])
    visible = card["visible_container_count"]
    total = card["total_container_count"]
    container_label = f"{visible} of {total} containers" if total != visible else (f"{visible} container" if visible == 1 else f"{visible} containers")
    appt = card["earliest_need_date"] or "No appt set"
    lfd_suffix = f" · LFD {card['earliest_lfd']}" if card["earliest_lfd"] else ""
    badges = ""
    if card["exception_count"]:
        badges += f'<span style="background:#fee2e2;color:#991b1b;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;margin-right:4px;">{card["exception_count"]} exception{"s" if card["exception_count"] != 1 else ""}</span>'
    if card["unassigned_count"]:
        badges += f'<span style="background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">{card["unassigned_count"]} unassigned</span>'
    html = f'<a href="{escape(card["workspace_url"])}" target="_blank" style="text-decoration:none;color:inherit;display:block;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-bottom:10px;background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.05);"><div style="font-weight:700;font-size:13px;color:#0f172a;">{escape(card["booking_number"])}</div><div style="font-size:11px;color:#475569;margin-top:2px;">{escape(card["customer"])} · {escape(card["move_type"])} · {escape(container_label)}</div><div style="font-size:11px;color:#475569;">{escape(display_status)} · {escape(appt)}{escape(lfd_suffix)}</div><div style="margin-top:6px;">{badges}</div></a>'
    st.markdown(html, unsafe_allow_html=True)
```
Replace with (drops `target="_blank"`/`<a href>` entirely; the whole card is still one visual block, now followed by a full-width Streamlit button that actually does the opening — this is the standard Streamlit pattern for a "clickable card" since raw HTML can't dispatch a Python callback):
```python
def _render_booking_card(card: dict) -> None:
    display_status = get_display_label(card["move_type"], card["canonical_status"])
    visible = card["visible_container_count"]
    total = card["total_container_count"]
    container_label = f"{visible} of {total} containers" if total != visible else (f"{visible} container" if visible == 1 else f"{visible} containers")
    appt = card["earliest_need_date"] or "No appt set"
    lfd_suffix = f" · LFD {card['earliest_lfd']}" if card["earliest_lfd"] else ""
    border_color = get_status_ui(card["canonical_status"])["border"]
    badges = ""
    if card["exception_count"]:
        badges += f'<span style="background:#fee2e2;color:#991b1b;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;margin-right:4px;">{card["exception_count"]} exception{"s" if card["exception_count"] != 1 else ""}</span>'
    if card["unassigned_count"]:
        badges += f'<span style="background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">{card["unassigned_count"]} unassigned</span>'
    html = f'<div style="border:1px solid #e2e8f0;border-left:4px solid {border_color};border-radius:10px;padding:10px 12px;margin-bottom:2px;background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.05);"><div style="font-weight:700;font-size:13px;color:#0f172a;">{escape(card["booking_number"])}</div><div style="font-size:11px;color:#475569;margin-top:2px;">{escape(card["customer"])} · {escape(card["move_type"])} · {escape(container_label)}</div><div style="font-size:11px;color:#475569;">{escape(display_status)} · {escape(appt)}{escape(lfd_suffix)}</div><div style="margin-top:6px;">{badges}</div></div>'
    st.markdown(html, unsafe_allow_html=True)
    if st.button("Open →", key=f"open_card_{card['group_id']}", use_container_width=True):
        st.session_state["dispatch_board_selected_row_ids"] = list(card["row_ids"])
        st.rerun()
```

- [ ] **Step 2: Add `render_booking_workspace`**

Add this new function directly above `def render_dispatch_board_focused(` (so it's defined before use, matching this file's existing ordering convention):
```python
def render_booking_workspace(booking_df: pd.DataFrame, refresh_callback: Callable[[], None] | None = None, port_houston_panel_renderer: Callable | None = None) -> None:
    """Booking-level wrapper around render_dispatch_workspace.

    A single-container booking renders render_dispatch_workspace directly
    — no extra tab layer, matching how active_status.py already opens it.
    A multi-container booking gets a compact header plus one tab per
    container, each tab rendering the exact same render_dispatch_workspace
    content — no second, thinner implementation of dispatch controls."""
    if booking_df.empty:
        st.warning("The selected load is no longer available.")
        return

    first = booking_df.iloc[0]
    booking_label = str(first.get("Booking Number", "") or "").strip() or f"Load {first.get('Load ID', '')}"
    customer = str(first.get("Customer", "") or "-")
    move_type = str(first.get("Dispatch Move Type", "") or first.get("TYPE", "") or "-")
    canonical_status = str(first.get("Status", "") or "New")
    container_count = len(booking_df)

    st.markdown(f"### Booking {booking_label}")
    st.caption(f"{customer} · {move_type} · {container_count} Container{'s' if container_count != 1 else ''}")
    st.markdown(render_status_badge(canonical_status), unsafe_allow_html=True)

    if container_count > 1:
        tab_labels = ["Booking Summary"] + [
            f"Container {i + 1} — {str(row.get('Container Number', '') or row.get('Load ID', '') or row.get('_row_id', ''))}"
            for i, (_, row) in enumerate(booking_df.iterrows())
        ]
        tabs = st.tabs(tab_labels)
        with tabs[0]:
            summary_cols = [c for c in ["Container Number", "Load ID", "Status", "Driver Name", "Truck Assigned", "Delivery Need Date", "LFD", "Exceptions"] if c in booking_df.columns]
            st.dataframe(booking_df[summary_cols], hide_index=True, use_container_width=True)
        for i, (_, row) in enumerate(booking_df.iterrows()):
            with tabs[i + 1]:
                render_dispatch_workspace(row, refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)
    else:
        render_dispatch_workspace(first, refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)
```

Add the import needed by this function near the top of the file (alongside the existing `from ui_components.status_badge import render_status_badge`, which is already imported — confirm with `grep -n "from ui_components.status_badge"`, it's already there at the existing import block, no change needed there). Add `get_status_ui` to the existing `from services.dispatch_workflow_service import (...)` block used by `_render_booking_card` (Step 1 above):
```python
from services.dispatch_workflow_service import (
    LOAD_STATUS_FLOW,
    _clean_display_value,
    _generate_driver_dispatch_message,
    _int_or_none,
    _load_exception_summary,
    _load_readiness_details,
    _normalize_load_type,
    _normalize_load_type_value,
    _safe_str,
    get_status_ui,
)
```

- [ ] **Step 3: Repoint the selection-handling block to the booking workspace**

Current code (end of `render_dispatch_board_focused`, lines 800-820):
```python
    selected_row_id = st.session_state.get("dispatch_board_selected_row_id")
    if selected_row_id is None:
        st.caption("Open any load card to review dispatch details, sync port data, request PIN, update status, or send the driver packet.")
        return

    selected_df = board_df[board_df["_row_id"].astype(int).eq(int(selected_row_id))].copy() if "_row_id" in board_df.columns else pd.DataFrame()
    if selected_df.empty:
        st.warning("The selected load is no longer available.")
        if st.button("Clear Dispatch Selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
        return

    clear_cols = st.columns([4, 1])
    with clear_cols[0]:
        st.markdown("### Selected Load")
    with clear_cols[1]:
        if st.button("Clear Selection", key="clear_dispatch_board_selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
    render_dispatch_workspace(selected_df.iloc[0], refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)
```
Replace with:
```python
    selected_row_ids = st.session_state.get("dispatch_board_selected_row_ids")
    if not selected_row_ids:
        st.caption("Open any booking card to review dispatch details, sync port data, request PIN, update status, or send the driver packet.")
        return

    selected_df = board_df[board_df["_row_id"].astype(int).isin([int(v) for v in selected_row_ids])].copy() if "_row_id" in board_df.columns else pd.DataFrame()
    if selected_df.empty:
        st.warning("The selected booking is no longer available.")
        if st.button("← Back to Dispatch Board", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_ids", None)
            st.rerun()
        return

    if st.button("← Back to Dispatch Board", key="clear_dispatch_board_selection"):
        st.session_state.pop("dispatch_board_selected_row_ids", None)
        st.rerun()
    render_booking_workspace(selected_df, refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)
```

Note: this leaves the legacy single-value key `dispatch_board_selected_row_id` (singular) entirely alone — nothing in this file sets it anymore after this change (the only prior setter was inside the row-based board, already removed in Phase 7), so no migration of that key is needed. `active_status.py` uses its own, unrelated session-state key for its own selection and is untouched.

- [ ] **Step 4: Verify**

Run:
```powershell
python -m compileall -q pages_app/dispatch_board.py
python -m pytest -q
```
Expected: compile exit 0; 112 tests pass.

Visually: click "Open →" on a single-container booking card — the full `render_dispatch_workspace` (all tabs, driver communication, notes) opens in the same tab, below the board, sidebar still visible. Click "← Back to Dispatch Board" — you land back on the Dispatch Board with Service Flow, Status Filter, and all secondary filters exactly as you left them (they're keyed widgets, so Streamlit already restores them from `st.session_state` — no extra code needed for this). Open the RICGX1235800 multi-container booking (once advanced past intake) — confirm it shows a "Booking Summary" tab plus one tab per container, each with the full dispatch control set.

- [ ] **Step 5: Commit**

```bash
git add pages_app/dispatch_board.py
git commit -m "Repoint booking cards to open the full dispatch workspace in-app instead of a thinner workspace in a new tab"
```

---

### Task 6: Retire the query-param Booking Workspace route

**Files:**
- Modify: `pages_app/router.py` (remove the `?booking=`/`?load_id=` branch and the `booking_detail` import)
- Delete: `pages_app/booking_detail.py`

**Interfaces:** none — pure removal, confirmed dead once this change lands.

**Confirmed before deleting (CLAUDE.md dead-code checklist, already run this session):** `grep -rn "booking_detail\|render_booking_detail"` across the repo returns only `pages_app/router.py` (the route being removed here), `pages_app/dispatch_board.py`'s own unrelated `workspace_url` string building (a leftover reference to the URL shape, not a call), the plan docs, and `pages_app/booking_detail.py` itself. Zero test files reference it. No dynamic `getattr`/callback-key access to it exists (it's only ever called positionally from `router.py`).

- [ ] **Step 1: Remove the route from `pages_app/router.py`**

Remove the import (line 13):
```python
from pages_app.booking_detail import render_booking_detail
```

Remove the query-param branch (lines 79-91, `route_selected_page`'s current body):
```python
def route_selected_page() -> None:
    selected_booking = st.query_params.get("booking", None)
    selected_load_id = st.query_params.get("load_id", None)
    if selected_booking or selected_load_id:
        df = _load_current_tms_data_or_stop()
        render_booking_detail(
            df,
            selected_booking,
            load_id=selected_load_id,
            refresh_callback=refresh_data,
            port_houston_panel_renderer=_render_load_port_houston_panel,
        )
        return

    section = render_sidebar(
        refresh_callback=refresh_data,
        status_legend_renderer=render_status_legend,
    )

    df = _load_current_tms_data_or_stop() if section in LOAD_DATA_SECTIONS else pd.DataFrame()
    _render_selected_page(section, df)
```
Replace with:
```python
def route_selected_page() -> None:
    section = render_sidebar(
        refresh_callback=refresh_data,
        status_legend_renderer=render_status_legend,
    )

    df = _load_current_tms_data_or_stop() if section in LOAD_DATA_SECTIONS else pd.DataFrame()
    _render_selected_page(section, df)
```

- [ ] **Step 2: Delete `pages_app/booking_detail.py`**

```bash
git rm pages_app/booking_detail.py
```

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
python -m pytest -q
```
Expected: compile exit 0 (confirms no remaining import of the deleted module); 112 tests pass.

Visually: navigate directly to a URL with `?booking=SOMETHING` appended — it should no longer produce a workspace page; it falls through to the normal sidebar-routed app (confirms the dead route is fully gone, not silently broken).

- [ ] **Step 4: Commit**

```bash
git add pages_app/router.py
git commit -m "Retire the query-param Booking Workspace route now that booking cards open the in-app workspace"
```

---

### Task 7: Full verification, manual acceptance pass, and requirements doc

**Files:**
- Create: `docs/DISPATCH_BOARD_UX_REQUIREMENTS.md`

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: 112 passed (101 original + 5 Task 1 + 6 Task 2).

- [ ] **Step 3: Manual acceptance pass**

Restart the running `streamlit run app.py` instance (large enough change across sessions to not trust hot-reload, per this repo's established precedent). Walk through:
1. Dispatch Board: confirm Service Flow directly above Status Filter, both compact, above secondary filters.
2. Confirm Service Flow only offers All/Import/Export/Local Import/Local Export (re-confirms Task-independent finding that the `ImportS` bug is not present).
3. Confirm "Operational Summary" is collapsed with zero exceptions in view, auto-expands when an exception-bearing load is in view.
4. Open a single-container booking card — full dispatch workspace (all tabs) opens in-app, sidebar stays visible.
5. Click "← Back to Dispatch Board" — filters are exactly as left.
6. Open the RICGX1235800 multi-container booking — Booking Summary tab + one tab per container, each with full controls.
7. Confirm an Import or Export load's workspace shows "Port Sync / PIN"; a Local Import or Local Export load's workspace does not.
8. Confirm the new "Notes" tab holds Operational Notes (saveable with history) and a read-only Dispatcher Notes reference.
9. Confirm status badge colors are visibly distinct for Ready to Dispatch / En Route to Pickup / At Pickup / En Route to Delivery / At Delivery / Returning Empty / Completed.
10. Confirm navigating to a stale `?booking=` URL no longer opens a separate thin workspace.

- [ ] **Step 4: Write `docs/DISPATCH_BOARD_UX_REQUIREMENTS.md`**

```markdown
# Dispatch Board UX Requirements

## Filter order
1. Service Flow (`ui_components/flow_filters.py::render_service_flow_filter`) — canonical options from `services/workflow_constants.SERVICE_FLOW_FILTER_OPTIONS` (`All`, `Import`, `Export`, `Local Import`, `Local Export`). No free-text entry is possible.
2. Status Filter — canonical options from `services/dispatch_stages.SHARED_STAGES` via `services/dispatch_board_view.get_board_columns()`.
3. Secondary filters (Board Scope, Exceptions Only, Search, Customer, Driver, Port, Warehouse) — compact row below.
4. Collapsible "Operational Summary" (KPIs) — collapsed by default, auto-expands when the current filtered view has at least one active exception.
5. Status lanes (swimlane board, one per `SHARED_STAGES` entry, `Completed` shown separately).

## Status color key
Single source: `services/dispatch_workflow_service.STATUS_UI` (`get_status_ui(status)` → `{background, border, text}`). `STATUS_COLORS` and `_get_status_border_color()` are derived views kept for existing callers — do not hand-edit them separately from `STATUS_UI`.

## Booking card fields
Booking number, status badge, customer, service flow, container count (`N of Total` when a booking has containers outside the current filter), earliest appointment date, LFD, exception badge, unassigned badge, left border colored by `get_status_ui(status)["border"]`. One card per booking group (`services/dispatch_card_view_model.build_booking_card_view_models`) — never one card per container row.

## Port/PIN rule
`services/workflow_constants.requires_port_pin(service_flow) -> bool` — `True` only for `Import`/`Export`. The Booking Workspace's "Port Sync / PIN" tab is omitted entirely (not hidden-but-present) for `Local Import`/`Local Export`. Backend readiness/exception logic (`services/dispatch_workflow_service._load_requires_port_type`) already delegates to the same function — missing-PIN exceptions and the "No PIN" KPI only ever count Import/Export loads.

## Booking Workspace
Single canonical implementation: `pages_app/dispatch_board.py::render_dispatch_workspace` (per-container: Dispatch Details, Port Sync/PIN [Import/Export only], Status Update, Timeline, Driver Notes/Text, Customer Notes, Notes [Operational + Dispatcher reference], Documents, Billing), wrapped by `render_booking_workspace` for multi-container bookings (compact header + Booking Summary tab + one tab per container). `pages_app/booking_detail.py` and the `?booking=`/`?load_id=` query-param route are retired.

## Back navigation
Booking cards set `st.session_state["dispatch_board_selected_row_ids"]` and rerun — no query params, no new tab. "← Back to Dispatch Board" pops that key and reruns. Every other Dispatch Board filter is a keyed Streamlit widget, so its value survives the rerun automatically — no explicit filter-restore code is needed or present.
```

- [ ] **Step 5: Commit**

```bash
git add docs/DISPATCH_BOARD_UX_REQUIREMENTS.md
git commit -m "Document the Dispatch Board UX requirements: filter order, status colors, Port/PIN rule, workspace, back nav"
```
