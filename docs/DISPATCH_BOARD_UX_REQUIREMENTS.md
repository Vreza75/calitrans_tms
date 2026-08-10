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
