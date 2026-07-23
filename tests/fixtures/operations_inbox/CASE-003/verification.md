# Operations Inbox Case Acceptance Audit — CASE-003

New Local Import.

## Expected vs actual (field-by-field)

Passed on the first clean run - no code changes were required (the only
implementation gaps this case depended on, `"Order Number"` as a
Booking Number alias and `"Pickup Address"` as a Customer Pickup Address
alias, were added while writing the fixture, before the first pipeline
run - see Git diff summary).

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Local Import | Local Import | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | LI-260803 | LI-260803 | yes |
| container_count | 1 | 1 | yes |
| containers | [HJCU2468101] | [HJCU2468101] | yes |
| customer | Houston Home Supply | Houston Home Supply | yes |
| pickup.terminal | Houston Transload Warehouse | (same) | yes |
| pickup.customer_pickup_address | 8700 Wallisville Road, Houston, TX 77029 | (same) | yes |
| delivery.warehouse | Houston Home Supply Distribution Center | (same) | yes |
| delivery.address | 15500 North Freeway, Houston, TX 77090 | (same) | yes |
| dates.delivery_need_date | August 6, 2026 | August 6, 2026 | yes |
| references.container_size | 40FT | 40FT | yes |
| references.contact_name | Melissa Grant | Melissa Grant | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100%
- Overall field accuracy: 100%
- Exact-record result: PASS

## Required checks (from the case spec)

- Correctly classified as **Local Import** (not port Import): confirmed via
  `test_case_003_does_not_classify_as_port_import` -
  `_infer_type()` matches the literal phrase `"local import"` in the body
  before it ever checks the generic `"import"` fallback, so this ordering
  already protected against misclassification; no fix needed.
- No terminal/vessel/port-PIN data required: confirmed - `Document Cutoff`/
  `LFD` stayed empty and the case still reached `Create New Order` with
  100% critical-field accuracy; nothing in the pipeline requires those
  fields to be populated for a Local Import to proceed.
- Origin and destination preserved distinctly: confirmed -
  `pickup.terminal`/`pickup.customer_pickup_address` (origin) vs.
  `delivery.warehouse`/`delivery.address` (destination) are populated from
  different body labels and don't collide.
- Exactly one order/container: confirmed (`container_count: 1`).

## Database records

- 1 `order_intake` row, 0 `loads` rows (pending dispatcher approval).
- Duplicate rerun: row count unchanged (1 before, 1 after) - PASS.

## Regression test

`tests/integration/operations_inbox/test_case_003_new_local_import.py`
- `test_case_003_passes_clean`
- `test_case_003_rerun_creates_no_duplicates`
- `test_case_003_is_deterministic_across_independent_runs`
- `test_case_003_does_not_classify_as_port_import`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
covers CASE-000 through CASE-003 - 14 tests): 14/14 passed each time.
Full suite: 284 passed, 14 skipped - zero regressions vs. baseline.
Reprocessed the same email twice: record count stayed at 1 both times.
Two independent full CLI runs: deterministic, both PASSED.

Note: one unrelated full-suite run hit a transient
`psycopg2.OperationalError` in `test_communications_schema.py` (likely
connection contention from running many scratch-DB CLI invocations back to
back); it passed immediately on rerun in isolation and on a subsequent full
run, so it isn't attributed to this case's changes.

## Git diff summary

Two small, additive parser-alias gaps found while transcribing the case
spec into a fixture (found by inspection before the first live run, not by
a failing diff):

1. **`services/email_parser.py` - `LABEL_ALIASES["Booking Number"]`**:
   added `"Order Number"`, `"Order #"`, `"Order No"` - Local Import/Export
   moves use customer "order numbers," not ocean "booking numbers," but the
   two map to the same `booking_number` concept in `expected.json`.
2. **`services/email_parser.py` - `LABEL_ALIASES["Customer Pickup Address"]`**:
   added `"Pickup Address"` - Local Import/Export emails have a single
   pickup address (no separate empty-depot/shipper distinction like
   Export), reusing the same field CASE-002 introduced.

Both are alias-list additions only; no branching logic changed.

## Decision

**ACCEPTED**
