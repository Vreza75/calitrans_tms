# Orders Management: Ready to Dispatch Step

## Context

Orders Management (`pages_app/orders_management.py`, wired via `render_orders_management`
in `pages_app/router.py`) currently has four queues: New, Missing Info, Booking
Verified, Cancel. The shared order editor (`_render_order_detail_editor`) shows
Driver Name / Truck Assigned / Chassis on every order regardless of queue, even
though those fields are meaningless before a booking is verified.

The user wants an explicit final step that moves an order from `Booking
Verified` to `Ready to Dispatch`, with driver/truck/chassis/phone assignment
happening only at that step, and a driver-ready text message generated at the
same time. `Ready to Dispatch` is already a real canonical status — the first
stage of `SHARED_STAGES` in `services/dispatch_stages.py` — and Orders
Management currently has no path that sets it; loads either arrive at that
status some other way or sit at `Booking Verified` indefinitely.

**Discovered during design, changes the original ask:** there is no
`driver_phone` field anywhere in the app today, but there IS an existing
canonical Drivers roster (`drivers` table, managed via Admin -> Drivers /
`pages_app/admin.py`) with `driver_name`, `phone`, `truck_number`, `status`.
Rather than add a duplicate `driver_phone` column to `loads` (which would
drift from the roster), the new step looks up phone/truck from this existing
roster by driver name. No database migration is needed for this feature.

## Status flow

```
Booking Verified  --[dispatcher fills in driver/truck/chassis, hits button]-->  Ready to Dispatch
```

`Ready to Dispatch` is already `is_active_dispatch_status(...) == True`
(services/dispatch_board_view.py), so once an order reaches this status it
immediately shows up on the Dispatch Board as an active load, exactly as it
would if set from anywhere else. No changes to the Dispatch Board are in
scope for this spec.

For Import/Export loads, the Dispatch Board's existing readiness model
(`_load_readiness_details` in `services/dispatch_workflow_service.py`) also
expects Port Verified + PIN/Appointment before a load is fully
`dispatchable`. Those steps happen later, in the Dispatch Board's load
workspace, and are **not** gated here — if a dispatcher marks Ready to
Dispatch before Port Verified/PIN are done, a warning is shown but the action
is allowed. The load will then correctly surface a "No PIN / appointment"
exception on the Dispatch Board, which is expected and already-existing
behavior, not a bug introduced by this feature.

## Booking Verified queue changes

`_render_order_detail_editor` (used by New, Missing Info, Booking Verified,
and Cancel queues) drops the Driver Name / Truck Assigned / Chassis fields
entirely, from both the form display and the save payload. These queues only
edit booking/order information. `ORDER_MANAGEMENT_STATUSES` gains `"Ready to
Dispatch"` (with a matching label) so the Status column/dropdown displays it
correctly wherever an order already carries that status.

## New "Ready to Dispatch" queue

A 5th queue tab, added to `queue_options` / `queue_map` in
`render_orders_management`. Its source list is the same as Booking Verified
(`work_df[Status == "Booking Verified"]`) — these are the same underlying
orders; the difference is the action panel shown when a row is selected.

`render_clickable_order_table` is parameterized with which detail-panel
function to call for the given queue, so "Ready to Dispatch" uses a new
`_render_ready_to_dispatch_panel` function instead of
`_render_order_detail_editor`.

### `_render_ready_to_dispatch_panel(work_df, selected_row_id, context_key)`

1. **Read-only order summary**: booking #, container #, customer, port,
   warehouse, delivery need date, LFD — same fields/style as the existing
   booking-detail expander pattern already used elsewhere in this file.

2. **Driver select**: `st.selectbox` populated from
   `driver_roster_service.list_active_drivers()` (roster rows with
   `status == "Active"`, ordered by driver name), formatted as `"{driver_name}
   ({truck_number})"`. Includes an `"Other / not in roster"` option that
   reveals a free-text Driver Name input as a fallback for a driver who
   isn't in the roster yet, plus the load's current Driver Name value if
   it's already set and doesn't match any roster entry (so existing data
   isn't silently dropped from the dropdown). Selecting a roster driver
   auto-fills the Truck Assigned and Phone fields below (via session state
   defaults); both remain independently editable afterward.

3. **Truck Assigned**: text input, pre-filled from the roster selection.

4. **Chassis**: plain text input (not roster-backed — chassis is a
   per-load/per-trip assignment, not a driver attribute).

5. **Driver Phone**: text input/display, pre-filled from the roster
   selection when available; editable; optional (not required to proceed).
   Not persisted to `loads` — it is derived from the roster each time this
   panel is opened, so `loads` never stores a phone number that could drift
   from the roster's.

6. **Readiness warning**: if `_load_requires_port(selected_load)` is true
   (Import/Export) and `_load_port_verified(...)` or
   `_load_has_pin_or_appointment(...)` is false, show
   `st.warning("Port Verified / PIN is not complete yet. This load can still
   be marked Ready to Dispatch, but the Dispatch Board will flag it as an
   exception until Port Sync / PIN is done.")`. Local Import/Local Export
   never trigger this warning (`requires_port_pin` is false for them).

7. **Generated Dispatch Message preview**: reuses the existing
   `_generate_driver_dispatch_message` (services/dispatch_workflow_service.py)
   — the same canonical message the Dispatch Board's load workspace already
   produces — fed a merged view of the selected load's row with the
   in-progress Driver Name / Truck Assigned / Chassis values overlaid, shown
   in a read-only `st.text_area` for copy/paste. PIN/appointment/empty-return
   lines will show `"-"` at this stage, matching how the message already
   renders for any load missing those fields.

8. **"Mark Ready to Dispatch" button**: disabled unless Driver Name, Truck
   Assigned, and Chassis are all non-blank (phone stays optional). On click:
   `DispatchDatabaseClient().update_row_fields(row_id, {"Driver Name":
   ..., "Truck Assigned": ..., "Chassis": ..., "Status": "Ready to
   Dispatch", "Dispatcher Notes": ...})`, then refresh + rerun, matching the
   existing action-button pattern used by every other queue action in this
   file.

## New backend piece: `services/driver_roster_service.py`

Small, single-purpose module (business/domain logic — belongs in
`services/`, not inline in the page per the project's architecture rules):

- `find_driver_in_roster(drivers_df: pd.DataFrame, driver_name: str) -> dict
  | None` — **pure function**, no I/O. Case-insensitive match against a
  `driver_name` column; returns a dict with `phone` / `truck_number` /
  `status` for the first match, or `None`. Fully unit-testable without a
  database.
- `list_active_drivers() -> pd.DataFrame` — thin `read_df`-backed query
  (`select driver_name, phone, truck_number from drivers where status =
  'Active' order by driver_name`), following the same import convention as
  `services/dispatch_data_service.py` (`from db_client import read_df`).

This split mirrors the pure-helper-plus-thin-I/O-wrapper pattern already
established today by `build_search_blob` (services/dispatch_workflow_service.py) —
one canonical, testable function instead of ad hoc inline lookups.

## Explicitly out of scope

- No changes to the Dispatch Board itself (`pages_app/dispatch_board.py`,
  the load workspace's own dispatch-message panel).
- No database migration — no new columns.
- No changes to the Drivers roster admin page.
- No enforcement/blocking of Port Verified/PIN before Ready to Dispatch —
  warn-only, per explicit decision during design.

## Testing plan

- `tests/test_driver_roster_service.py`: unit tests for
  `find_driver_in_roster` covering: match found, case-insensitive match, no
  match, empty roster, multiple roster rows with only one matching, blank/
  None driver name input. `list_active_drivers` is a thin I/O wrapper and is
  not unit tested directly (no test DB in this environment); it is covered
  by the manual acceptance test below.
- Existing `tests/test_dispatch_stages.py`, `tests/test_dispatch_board_view.py`
  unaffected — no changes to canonical stage logic.
- Manual acceptance test (Streamlit UI): move an order through New ->
  Booking Verified, confirm the Booking Verified editor no longer shows
  Driver/Truck/Chassis, open the new Ready to Dispatch tab for that order,
  pick a roster driver and confirm truck/phone auto-fill, edit chassis,
  confirm the generated message preview updates live, click Mark Ready to
  Dispatch, confirm the order disappears from both Orders Management queues
  and appears on the Dispatch Board as a Ready to Dispatch card with the
  assigned driver/truck/chassis.
