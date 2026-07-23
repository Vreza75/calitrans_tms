# Operations Inbox Case Acceptance Audit — CASE-002

New Export, Single Container.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Export | Export | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | LSF-EXP-260802 | LSF-EXP-260802 | yes |
| container_count | 1 | 1 | yes |
| containers | [TGHU7654321] | [TGHU7654321] | yes |
| customer | Lone Star Foods | Lone Star Foods | yes |
| pickup.terminal | Barbours Cut Terminal | Barbours Cut Terminal | yes |
| pickup.empty_pickup | Barbours Cut Empty Depot | (same) | yes |
| pickup.customer_pickup | Lone Star Foods Export Warehouse | (same) | yes |
| pickup.customer_pickup_address | 2200 Navigation Boulevard, Houston, TX 77003 | (same) | yes |
| delivery | {} (must stay empty - hard rule) | {} | yes |
| dates.pickup_date | August 5, 2026 | August 5, 2026 | yes |
| dates.document_cutoff | August 7, 2026 | August 7, 2026 | yes |
| references.container_size | 40HC | 40HC | yes |
| references.contact_name | Thomas Lee | Thomas Lee | yes |
| references.contact_email | thomas.lee@example.com | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100%
- Overall field accuracy: 100%
- Exact-record result: PASS

## Required checks (from the case spec)

- Classified as **Export**, not Import: confirmed (`service_flow: Export`,
  `TYPE` inferred from `"export"` appearing in subject/body, no `"import"`
  anywhere in the fixture).
- Empty-pickup, customer-pickup, and terminal preserved as **three distinct**
  values (`pickup.empty_pickup`, `pickup.customer_pickup`,
  `pickup.terminal`) - not merged: confirmed by
  `test_case_002_export_pickup_locations_stay_distinct_from_delivery`.
- Customer pickup warehouse NOT interpreted as the final local-delivery
  warehouse: confirmed - `delivery == {}` (the `Warehouse`/`Address` parser
  fields, which represent local delivery, stayed empty because
  `Customer Pickup Location:`/`Customer Pickup Address:` don't match any
  `Warehouse`/`Address` alias - full-line-anchored label matching prevents
  the collision).
- Exactly one operational container: confirmed (`container_count: 1`,
  `containers: [TGHU7654321]`).

## Database records

- 1 `order_intake` row, 0 `loads` rows (pending dispatcher approval, by
  design - this case validates intake, not the approval step).
- Duplicate rerun: row count unchanged (1 before, 1 after) - PASS.

## Regression test

`tests/integration/operations_inbox/test_case_002_new_export_single_container.py`
- `test_case_002_passes_clean`
- `test_case_002_rerun_creates_no_duplicates`
- `test_case_002_is_deterministic_across_independent_runs`
- `test_case_002_export_pickup_locations_stay_distinct_from_delivery`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
covers CASE-000/001/002 - 10 tests): 10/10 passed each time.
Full suite: 284 passed, 10 skipped (no `INBOX_CERTIFICATION_DATABASE_URL` set)
- zero regressions vs. baseline.
Reprocessed the same email twice: record count stayed at 1 both times.
Two independent full CLI runs of all three cases: deterministic, all PASSED.

## Git diff summary

Real gaps found and fixed while certifying this case:

1. **`services/email_parser.py`**: added four new parser fields - `Empty
   Pickup`, `Customer Pickup`, `Customer Pickup Address`, `Pickup Date` -
   plus an `"Export Terminal"` alias on the existing `Port` field. Export
   bookings have three genuinely distinct locations (empty-container depot,
   shipper's pickup warehouse, and the return terminal) that the
   Import-shaped `Warehouse`/`Address` fields cannot represent without
   violating the case's own rule against conflating them with the final
   delivery point. Purely additive - `FIELDS`/`LABEL_ALIASES` have no other
   consumers outside `email_parser.py` itself, so nothing existing could
   break from the new keys.
2. **`services/operations_inbox_service.py` - `_prepare_operations_email_record`**:
   `parse_email_text(subject, latest_body)` never passed `sender`, so
   `Contact Email`/`Contact Name` could only come from an explicit label in
   the body, not the `From` header - every other call site in the codebase
   (`operations_case_service.py`, `order_intake.py`) already passes `sender`.
   Fixed to `parse_email_text(subject, latest_body, sender)`. This also
   incidentally fixed CASE-000's previously-documented "Thank you," contact
   name bug, since sender-header identity now wins over the flawed
   signature-line fallback.

## Harness change

`tests/integration/operations_inbox/harness.py`'s `pickup`/`delivery`/
`dates`/`references` dicts are now **sparse** (only populated keys are
included, via a new `_sparse()` helper) instead of a fixed set of keys
padded with `null`. This is what let CASE-002 add `empty_pickup`/
`customer_pickup`/`customer_pickup_address`/`pickup_date`/`document_cutoff`
without reshaping CASE-000/CASE-001's already-accepted `expected.json` -
their fixtures only needed the two contact-field corrections above (real
behavior changes), not schema churn.

## Decision

**ACCEPTED**
