# Multi-Container Booking Row Grouping

## Context

The multi-container feature (built earlier this session) creates one `loads`
row per container, linked by `parent_booking_key` and numbered by
`container_sequence`/`container_total`. Today, `pages_app/orders_management.py`
and `pages_app/dispatch_board.py` render one table/card row per `loads` row —
so a 4-container booking shows as 4 separate lines, with no indication
they're related.

Verified by reading the code:

- `services/tms_data_service.get_ext_df()` selects a fixed whitelist of
  extended columns from `loads` (`EXT_LOAD_COLUMNS`) that does **not**
  currently include `parent_booking_key`, `container_sequence`, or
  `container_total` — these columns exist on the `loads` table (added by
  `database/multi_container_migration.sql`) but aren't exposed to the
  Streamlit pages' dataframe yet.
- `pages_app/orders_management.py`'s `render_clickable_order_table()`
  already uses `st.dataframe(..., selection_mode="single-row",
  on_select="rerun")` — clicking a row sets a `_row_id` in session state and
  reveals `_render_order_detail_editor()` below via `st.divider()`. This
  plan extends that exact pattern rather than inventing a new one.
- `pages_app/dispatch_board.py`'s `render_dispatch_board_focused()` renders
  one `_render_dispatch_action_card()` per load, grouped into lanes by
  `Dispatch Lane`/`Dispatch Action` (computed per-row from
  `_dispatch_action_metadata()`). Containers in the same booking can
  legitimately be at different dispatch stages (one dispatched, one still
  needs a driver) — this must never be hidden from the dispatcher.

## Decisions (from brainstorming)

- **Scope**: Orders/Load Management and Dispatch Board only. No other pages.
- **Grouping condition — Orders Management**: any group of `loads` rows
  sharing the same non-null `parent_booking_key` always collapses to one
  row.
- **Grouping condition — Dispatch Board**: a group only collapses if every
  row in it has the *same* `Status`. The instant they diverge, all rows in
  that group render individually again (never hidden). This is re-evaluated
  live from the current dataframe on every render — there's no separate
  "collapsed" state to fall out of sync.
- **Indicator**: a simple count badge, e.g. `"4 containers"`. Not a
  created/assigned progress count.
- **Interaction**: clicking a collapsed row does not jump straight to the
  single-load editor. It reveals a small sub-table listing that booking's
  individual containers (booking number, container number if assigned,
  status). Clicking one of those rows opens the existing single-load editor
  — unchanged from today.
- Single-container bookings (the majority of loads — anything with
  `parent_booking_key` null) render exactly as they do today. Zero visual
  or behavioral change for them.

## Design

### 1. Expose the grouping columns (`services/tms_data_service.py`)

Add `parent_booking_key`, `container_sequence`, `container_total` to
`EXT_LOAD_COLUMNS` and to the `select` list in `get_ext_df()`. These flow
through `merge_ext()` automatically (it already iterates `EXT_LOAD_COLUMNS`
generically) — no other change needed in that file.

### 2. Grouping helper (new `services/load_grouping_service.py`)

```python
def group_loads_by_booking(
    df: pd.DataFrame,
    *,
    require_same_status: bool = False,
) -> pd.DataFrame:
    """Collapse rows sharing a non-null parent_booking_key into one summary
    row per booking, adding a "Containers" badge column and a
    "_grouped_row_ids" column (list[int]) of the underlying _row_id values.

    Rows with no parent_booking_key pass through unchanged (single-row
    "group" of size 1, no badge).

    If require_same_status is True (Dispatch Board), a group only collapses
    when every row shares the same Status value — otherwise its rows pass
    through individually, unchanged, exactly as if parent_booking_key were
    null.
    """
```
- Pure function, no Streamlit/DB imports — testable with a plain DataFrame,
  matching the pattern of other `services/*_service.py` modules already in
  this codebase.
- The summary row for a collapsed group takes its shared display fields
  (Booking Number, Customer, Port, Warehouse, dates, TYPE, and — for
  Dispatch Board — Status) from the first row in the group, since those are
  set identically across the group's containers by
  `create_container_work_orders()`.
- `"Containers"` badge column: `f"{count} containers"` when count > 1,
  empty string when count == 1 (so it doesn't clutter single-container
  rows).

### 3. Orders Management (`pages_app/orders_management.py`)

In `render_clickable_order_table()`:
- Before building `display_cols`/sorting, call
  `group_loads_by_booking(table_df)` and add `"Containers"` to the displayed
  `columns` list.
- On row selection: if the selected row's `_grouped_row_ids` has more than
  one id, render a sub-table (same `st.dataframe` + `selection_mode`
  pattern) of just those `_row_id`s from `work_df` instead of immediately
  calling `_render_order_detail_editor()`. Selecting a row in *that*
  sub-table calls `_render_order_detail_editor()` as today.
- If `_grouped_row_ids` has exactly one id (ungrouped row), behavior is
  identical to today — call `_render_order_detail_editor()` directly.

### 4. Dispatch Board (`pages_app/dispatch_board.py`)

In `render_dispatch_board_focused()`, after `board_df` gains its computed
`Dispatch Lane`/`Dispatch Action`/etc. columns:
- Call `group_loads_by_booking(board_df, require_same_status=True)` per lane
  before rendering cards.
- A collapsed group renders as **one** `_render_dispatch_action_card()` with
  the `"4 containers"` badge added to the card's existing markup, and its
  "Work Load" button reveals the sub-table of containers (same pattern as
  Orders Management) instead of opening `open_load_workspace_dialog()`
  directly. Selecting one container from that sub-table opens the workspace
  dialog for that specific load, unchanged.
- A group that fails the same-status check renders as individual cards,
  exactly as today — this is the safety behavior verified in brainstorming.

## Testing

- `services/load_grouping_service.py` is pure and gets real unit tests:
  a 4-row same-booking group collapses to 1 summary row with `"4
  containers"`; a booking with mixed statuses does **not** collapse when
  `require_same_status=True` but does collapse when `False`; rows with no
  `parent_booking_key` are never grouped together even if several happen to
  share the same (empty) key value; the summary row's shared fields match
  the first row in the group.
- No new tests for the two page files — same reasoning as prior UI-only
  batches, no test harness exists for Streamlit page rendering in this
  project. Verification is `compileall` + running the app + visual check.

## Out of scope

- Any other page (Active Status, Dashboard, etc.).
- Changing how `create_container_work_orders()` creates child loads.
- A "created vs required" progress indicator (explicitly declined in favor
  of the simple count).
