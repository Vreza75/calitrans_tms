# Dispatch Board Workflow Specification (Phase 2)

## Status of this document

Phase 2 deliverable per the Dispatch Board Redesign request. Defines the
canonical workflow model before any backend or UI code is written (Phase 3+).
Builds on the Phase 1 repository review (see conversation) and the confirmed
decision to split operational and closeout status into two columns.

## Storage model

- `loads.status` (existing column, reused) becomes **operational status
  only** — physical movement, from Ready to Dispatch through Dispatch
  Complete. No billing/document values live here going forward.
- New `loads.closeout_stage` (text, nullable, default `'Not Started'`) holds
  billing/closeout progress independently. A load can be
  `status = 'Dispatch Complete'` and `closeout_stage = 'POD Needed'` at the
  same time — this is the case the single-column model couldn't represent.
- Order intake/verification statuses (`New`, `Hold/Need Info`,
  `Booking Verified`, etc.) continue to live in `loads.status` **before**
  a load is operationally active — confirmed empirically (see below) that
  `New Email`/`Needs Review`/`Order Created` never actually appear as
  `loads.status` values; those are Operations Inbox/`order_intake` concepts
  and are out of scope for this column.

## Canonical operational stages (`loads.status`, post-redesign)

Shared board stages (used for the "All Service Flows" view and metrics):

```
Ready to Dispatch → Assigned → En Route to Pickup → At Pickup →
En Route to Delivery → At Delivery → Empty Return → Completed
```

Per-move-type stage sequences (the actual values `loads.status` takes for
that move type — contextual naming, no generic "At Warehouse"):

**Import** (port → warehouse):
```
Ready to Dispatch → Driver Assigned → En Route to Port → At Port →
Container Picked Up → En Route to Delivery Warehouse → At Delivery Warehouse →
Delivered → Returning Empty → Empty Returned → Dispatch Complete
```
`Returning Empty`/`Empty Returned` are skipped (load goes straight from
`Delivered` to `Dispatch Complete`) when `empty_return_required = false`
(see Required Fields below).

**Export** (warehouse → port):
```
Ready to Dispatch → Driver Assigned → En Route to Pickup Warehouse →
At Pickup Warehouse → Container Loaded → En Route to Port → At Port →
In-Gated → Dispatch Complete
```
In-Gated is the physical-completion milestone for exports (matches your
spec: "considered physically complete when successfully in-gated").

**Local Import / Local Export** (warehouse → warehouse, shared sequence):
```
Ready to Dispatch → Driver Assigned → En Route to Origin Warehouse →
At Origin Warehouse → Loaded / Picked Up → En Route to Destination Warehouse →
At Destination Warehouse → Delivered → Dispatch Complete
```
Local Import and Local Export share this workflow shape but remain separate
`Service Flow` values for filtering/reporting — confirmed via live data this
session that `type` values normalize cleanly to `Local Import`/`Local
Export` via the existing alias table in `workflow_constants.py`; they are
not database-model duplicates of each other or of the plain Import/Export
flows.

## Canonical closeout stages (`loads.closeout_stage`)

```
Not Started → POD Needed → POD Received → Documents Review →
Accessorial Review → Rate Verification → Ready to Invoice → Invoice Sent →
Ready for ProfitTools → Closed
```

A load's `closeout_stage` advances to `POD Needed` automatically the moment
`status` reaches the move type's physical-completion milestone
(`Dispatch Complete` for Import/Local, `In-Gated`→`Dispatch Complete` for
Export) — this is the "auto-create closeout task" behavior from your spec,
implemented as a stage transition rather than a separate task record, since
`loads.closeout_stage` already gives every load exactly one closeout task by
construction (no separate task table needed, no risk of duplicates).

## Legacy status mapping

Grounded in the actual distinct `(status, type)` pairs present in the live
`loads` table (queried this session — 34 distinct combinations across New,
Hold/Need Info, Booking Verified, Ready to Dispatch, Assigned, En Route to
Pickup, At Pickup, Loaded, En Route To Delivery, Delivered, Returning Empty,
POD Received, Ready for ProfitTools, Exported to ProfitTools, Invoiced,
Closed, Cancelled — no verification-only statuses like Port Verified/PIN
Received/Awaiting Appointment currently have live rows, though they remain
valid pre-dispatch statuses per the code).

| Legacy `loads.status` | New `status` (operational) | New `closeout_stage` |
|---|---|---|
| New | *(pre-dispatch — Intake & Verification workspace, not on Dispatch Board)* | Not Started |
| Hold/Need Info | *(Intake & Verification)* | Not Started |
| Booking Verified | *(Intake & Verification)* | Not Started |
| Port Verified | *(Intake & Verification)* | Not Started |
| Ready for Appointment / PIN | *(Intake & Verification)* | Not Started |
| Ready for Port PIN | *(Intake & Verification)* | Not Started |
| PIN Received | *(Intake & Verification)* | Not Started |
| Awaiting Appointment | *(Intake & Verification)* | Not Started |
| Ready to Dispatch | Ready to Dispatch | Not Started |
| Driver Assigned | Driver Assigned (Import/Local) | Not Started |
| Assigned | Driver Assigned (Import/Local) | Not Started |
| Dispatched | *move-type entry point, see below* | Not Started |
| En Route to Pickup | En Route to Port (Import) / En Route to Pickup Warehouse (Export) / En Route to Origin Warehouse (Local) | Not Started |
| At Port | At Port | Not Started |
| At Pickup | At Pickup Warehouse (Export) / At Origin Warehouse (Local) | Not Started |
| Loaded / Picked Up | Container Picked Up (Import) / Container Loaded (Export) / Loaded / Picked Up (Local) | Not Started |
| Loaded | *(same as above)* | Not Started |
| En Route To Delivery | En Route to Delivery Warehouse (Import) / En Route to Port (Export) / En Route to Destination Warehouse (Local) | Not Started |
| Delivered | At Delivery Warehouse → Delivered (Import) / Delivered (Local) | POD Needed |
| Returning Empty | Returning Empty (Import only) | POD Needed |
| POD Received | Dispatch Complete | POD Received |
| Ready for ProfitTools | Dispatch Complete | Ready for ProfitTools |
| Exported to ProfitTools | Dispatch Complete | Ready for ProfitTools |
| Invoiced | Dispatch Complete | Closed |
| Closed | Dispatch Complete | Closed |
| Cancelled | Cancelled *(unchanged — cross-cutting, not part of either sequence)* | Not Started |

`Dispatched` is ambiguous in the legacy model (used generically for "driver
is moving") — maps to the move type's first "En Route" stage on migration;
going forward it's retired as a distinct value.

This mapping becomes a one-time backfill migration (`update loads set
closeout_stage = ... , status = ...` per the table above) plus a
compatibility function `map_legacy_status(old_status, move_type) -> (new_status,
closeout_stage)` kept in code so anything still reading old status strings
(reports, other pages) doesn't break before every call site is updated.

## Required fields by stage

| Stage | Requires |
|---|---|
| Ready to Dispatch | Customer, booking/container reference, origin, destination (existing `_load_readiness_details()` checklist, reused) |
| Driver Assigned / Assigned | Driver Name, Truck Assigned |
| En Route (any) | Valid origin for the direction of travel (e.g. can't go "En Route to Port" without a port/terminal set) |
| At Pickup / At Port / At Origin Warehouse | Must currently be Driver Assigned or later (can't skip straight from Ready to Dispatch) |
| Returning Empty (Import) | Must currently be Delivered |
| In-Gated (Export) | Must currently be At Port |
| Dispatch Complete | Must have reached the move type's physical-completion milestone (see per-type sequences above) |

## Invalid transitions (examples, not exhaustive — full table lives in code)

- Ready to Dispatch → Assigned/Driver Assigned without a driver: **blocked**.
- Anything → En Route without a valid origin for that leg: **blocked**.
- Anything → At Pickup/At Port before Assigned: **blocked**.
- Import: → Returning Empty before Delivered: **blocked**.
- Export: → In-Gated before At Port: **blocked**.
- Completed load (Dispatch Complete or Cancelled) → any operational status:
  **blocked** unless explicit override with a reason (recorded in
  `status_events.notes`).
- Skipping stages forward (e.g. Ready to Dispatch → Delivered) is blocked by
  default; an explicit "override with reason" path exists for real-world
  edge cases (e.g. backfilling a load that was tracked outside the system),
  logged distinctly from normal transitions in the audit trail.

## Exception model

Exceptions are **not** a status value and do not create a separate load.
They're rows tied to `load_id`, visible as an overlay on the existing card
regardless of which operational stage the load is in. Reuses the existing
`_load_exception_summary()` detection logic for the automatic ones (late
appointment, no driver, no PIN, port hold) and adds a manual "Report
Exception" action for driver-delay/mechanical/wrong-address/etc. A resolved
exception clears without touching `status`/`closeout_stage` — the load's
position on the board is entirely determined by its stage, never by whether
it currently has an open exception (an exception is an indicator drawn on
top of a card, not a lane).

## Billing handoff behavior

1. `status` reaches a move type's physical-completion milestone.
2. Transition service sets `status = 'Dispatch Complete'` (or Import's
   `Empty Returned → Dispatch Complete` if empty return was required) and,
   in the same call, sets `closeout_stage = 'POD Needed'` if it's still
   `'Not Started'`.
3. Load leaves all active Dispatch Board columns (filtered out by `status`
   alone — Board never filters on `closeout_stage`).
4. Load appears in Billing & Closeout, filtered/grouped by `closeout_stage`.
5. Dispatch completion is never blocked by missing POD/billing docs — the
   two columns are independent by construction, so there's no code path
   where closeout state can block an operational transition.

## Completion rules

- Import: `Dispatch Complete` reached via `Delivered` directly (no empty
  return required) or via `Delivered → Returning Empty → Empty Returned`
  (empty return required). `empty_return_required` is read from the load's
  existing data (empty return location/date fields already on `loads` —
  reused, not new) rather than a new flag: if an empty-return location is
  set, the empty-return leg is required.
- Export: `Dispatch Complete` reached via `In-Gated`.
- Local Import/Export: `Dispatch Complete` reached via `Delivered`.
- `Cancelled` is reachable from any operational stage before
  `Dispatch Complete` and is terminal.

## Out of scope for this spec

- The exact Streamlit rendering of the three workspaces (Phase 4/5).
- Exception severity scoring algorithm details (reuses existing detection,
  UI treatment TBD in Phase 4).
- Whether `services/workflow_status.py` gets deleted or revived — confirmed
  unused this session; final call deferred to Phase 6 cleanup after the new
  system is live, per your instruction not to remove without verifying no
  remaining need.
