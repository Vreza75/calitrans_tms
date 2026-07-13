# Dispatch Board Row Layout & Shared Canonical Stages

## Context

The Dispatch Board was redesigned earlier this session into a multi-column
Kanban layout (`services/dispatch_stages.py`, `dispatch_transition_service.py`,
`dispatch_legacy_status.py`, `dispatch_board_view.py`, and
`pages_app/dispatch_board.py`). The user reports two problems: (1) a literal
`<div style=` text bug on cards, fixed separately this session by collapsing
multi-line HTML style attributes to single-line; (2) the narrow-column layout
itself is hard to scan, wastes space on empty columns, and wraps content
excessively.

This spec replaces the column layout with a row-based board **and** changes
the underlying canonical status model: "Driver Assigned" and several other
per-move-type statuses (Delivered, Container Picked Up/Loaded, In-Gated) are
removed as distinct *stored* statuses, becoming either quick-action labels or
move-type-specific *display labels* over a smaller, fully shared canonical
list.

Verified this session: no live `loads` row currently uses the move-type-
specific canonical statuses built earlier (e.g. "En Route to Port") — every
row still holds a pre-existing legacy status (Assigned, At Pickup, Delivered,
etc.). This redesign therefore does not need to migrate away from anything
real; the legacy mapping just targets a different (simpler, shared) list.

## Canonical status model

`loads.status` stores exactly one of:
```
Ready to Dispatch
En Route to Pickup
At Pickup
En Route to Delivery
At Delivery
Returning Empty      (Import only — absent from Export/Local's valid stage list)
Completed
Cancelled            (cross-cutting, terminal, unchanged from today)
```
Same list for every move type — `get_operational_stages(move_type)` now
returns this list with `Returning Empty` included only for Import.

## Display labels (new, cosmetic only, never stored)

`services/dispatch_board_view.get_display_label(move_type, canonical_status, *, via_empty_return: bool = False) -> str`:

| Canonical | Import | Export | Local Import/Export |
|---|---|---|---|
| Ready to Dispatch | Ready to Dispatch | Ready to Dispatch | Ready to Dispatch |
| En Route to Pickup | En Route to Port | En Route to Pickup Warehouse | En Route to Origin Warehouse |
| At Pickup | At Port | At Pickup Warehouse | At Origin Warehouse |
| En Route to Delivery | En Route to Delivery Warehouse | En Route to Port | En Route to Destination Warehouse |
| At Delivery | At Delivery Warehouse | At Port | At Destination Warehouse |
| Returning Empty | Returning Empty | *(n/a)* | *(n/a)* |
| Completed | "Empty Returned" if `via_empty_return` else "Completed" | In-Gated | Completed |
| Cancelled | Cancelled | Cancelled | Cancelled |

`via_empty_return` is read from whether the load's last operational status
before Completed was `Returning Empty` (derivable from `status_events`, or
simpler: pass it explicitly as `True` when `apply_transition` is called
*from* the Returning Empty stage — the caller already knows this).

## Driver assignment — no longer a stage

- `validate_transition` no longer has an "assignment-gated stage" concept.
  Instead: transitioning **to** `En Route to Pickup` requires `has_driver`
  and `has_truck` — this is the only place assignment is enforced (same
  business rule as before, just checked at a different, correct point,
  since "Driver Assigned" is no longer an intermediate stage to pass
  through).
- `dispatch_transition_service.apply_transition` gains an optional
  `driver: str | None = None`, `truck: str | None = None` kwarg pair. When
  provided alongside a transition to `En Route to Pickup`, it writes the
  driver/truck fields *and* records a separate audit note
  ("Driver assigned: {name}") distinct from the status-change note — two
  audit events, not one conflated event, per the request's explicit
  requirement to preserve assignment history without treating it as a
  board stage.
- UI button label: `Assign & Start` when the load has no driver yet,
  `Start En Route` when it already does. Both call the same
  `apply_transition(..., new_status="En Route to Pickup", driver=..., truck=...)`
  — the only difference is whether the UI prompts for a driver first.

## Row layout

Replaces the per-stage `st.columns(len(columns))` board with one scrollable
list of full-width rows, each built from `st.columns([...])` with fixed
relative widths matching the request's 13-column layout (Priority/Risk,
Load, Move Type, Status, Origin, Destination, Appointment, Driver,
Truck/Chassis, ETA, Exceptions, Next Action, Open Load). Secondary fields
(full address, customer instructions, PIN, vessel cutoff, milestone history)
move into an `st.expander` per row rather than cluttering the row itself.

Sort order (default): severe exceptions → late appointments → due today →
empty returns due → ready-and-unassigned → active → future. Implemented as
a single composite sort key function, not nested UI logic.

Status filter (`st.selectbox` or `st.radio`, replacing the per-column
implicit split): All Active, Ready to Dispatch, En Route to Pickup, At
Pickup, En Route to Delivery, At Delivery, Returning Empty, Completed Today.
"Completed Today" remains a known gap (no completion timestamp tracked yet
— flagged again here, not solved in this batch) — it will show all
`Completed` loads, not just today's, with a note in the UI caption.

## Metrics

Ready to Dispatch, Unassigned, En Route (both legs combined), At Pickup, At
Delivery, Empty Returns Due (`Returning Empty` count), Active Exceptions,
Completed Today (same caveat as above — shows all Completed for now).
Removes the earlier "Assigned" concept entirely, per the request.

## Legacy status mapping (updated)

`services/dispatch_legacy_status.map_legacy_status(old_status, move_type)`
target changes to the new shared list — no longer move-type-branches on the
*target* value (only on whether `Returning Empty` is reachable and on
`closeout_stage`):

| Legacy | New canonical | closeout_stage |
|---|---|---|
| Ready to Dispatch | Ready to Dispatch | Not Started |
| Assigned / Driver Assigned | Ready to Dispatch (driver field already set — no longer implies a further stage) | Not Started |
| Dispatched / En Route to Pickup | En Route to Pickup | Not Started |
| At Port / At Pickup | At Pickup | Not Started |
| Loaded / Picked Up / Loaded | At Pickup | Not Started |
| En Route To Delivery | En Route to Delivery | Not Started |
| Delivered | Completed | POD Needed |
| Returning Empty | Returning Empty | POD Needed |
| POD Received | Completed | POD Received |
| Ready for ProfitTools / Exported to ProfitTools | Completed | Ready for ProfitTools |
| Invoiced / Closed | Completed | Closed |
| Cancelled | Cancelled | Not Started |
| pre-dispatch (New, Hold/Need Info, etc.) | "" (unchanged from before) | Not Started |

## Testing

Full rewrite of `tests/test_dispatch_stages.py` (shared list, no per-move-
type stage lists, assignment gate moved to the En Route transition),
`tests/test_dispatch_transition_service.py` (driver/truck kwargs, two-event
audit), `tests/test_dispatch_legacy_status.py` (new shared targets),
`tests/test_dispatch_board_view.py` (display-label function replaces
shared-stage mapping). All existing test *names* describing business rules
carry over conceptually (e.g. "cannot move to at-pickup before assigned")
even though the underlying stage list changed.

## Out of scope

- "Completed Today" proper timestamp-based filtering (flagged, not solved).
- Driver/Customer/Port/Warehouse filters beyond what already exists
  (Service Flow, Board Scope, Exceptions Only, Search) — can follow once
  the row layout is live and it's clear which are actually needed.
- Truck/chassis-required-per-move-type validation nuance ("review whether
  chassis should follow the same pattern... do not require chassis where
  not applicable") — deferred; current model doesn't require chassis for
  any transition today, so this is a non-regression, not a new gap.
