# Operations Inbox Case Acceptance Audit — CASE-004

New Local Export.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Local Export | Local Export | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | LE-260804 | LE-260804 | yes |
| container_count | 1 | 1 | yes |
| containers | [OOLU1357913] | [OOLU1357913] | yes |
| customer | Texas Industrial Packaging | Texas Industrial Packaging | yes |
| pickup.terminal | Texas Industrial Packaging | (same) | yes |
| pickup.customer_pickup_address | 1200 Industrial Parkway, Pasadena, TX 77503 | (same) | yes |
| delivery.warehouse | Southeast Container Yard | (same) | yes |
| delivery.address | 9500 Old Galveston Road, Houston, TX 77034 | (same) | yes |
| dates.pickup_date | August 7, 2026 | August 7, 2026 | yes |
| references.container_size | 20FT | 20FT | yes |
| references.contact_name | Carlos Ramirez | Carlos Ramirez | yes |
| references.contact_email | carlos.ramirez@example.com | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100%
- Overall field accuracy: 100%
- Exact-record result: PASS (first clean run - fixed before running, see
  Git diff summary)

## Required checks (from the case spec)

- Correctly classified as **Local Export**, no port route created:
  confirmed (`service_flow: Local Export`, no `Document Cutoff`/`LFD`
  populated, no port-only data required for `Create New Order`).
- Pickup and local delivery preserved distinctly: confirmed -
  `pickup.terminal`/`pickup.customer_pickup_address` (Texas Industrial
  Packaging) vs. `delivery.warehouse`/`delivery.address` (Southeast
  Container Yard) come from different labels.

## Database records

- 1 `order_intake` row, 0 `loads` rows (pending dispatcher approval).
- Duplicate rerun: row count unchanged (1 before, 1 after) - PASS.

## Regression test

`tests/integration/operations_inbox/test_case_004_new_local_export.py`
- `test_case_004_passes_clean`
- `test_case_004_rerun_creates_no_duplicates`
- `test_case_004_is_deterministic_across_independent_runs`
- `test_case_004_facility_name_is_not_stripped_as_a_person_name`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-004 - 18 tests): 18/18 passed each time.
Full suite: 284 passed, 18 skipped - zero regressions vs. baseline.
Reprocessed the same email twice: record count stayed at 1 both times.
Two independent full CLI runs: deterministic, both PASSED.

## Git diff summary

Found by direct inspection while transcribing the fixture (verified with
`_invalid_location_value("Texas Industrial Packaging")` before the first
live run - caught before it could produce a failing diff):

1. **`services/email_parser.py` - `_looks_like_person_name`**: this is the
   same false-positive class as CASE-001's "Gulf Coast Retail DC" -
   "Texas Industrial Packaging" is 3 title-case words with no location
   keyword, so it read as a person's name and would have been stripped
   from `Port`/`Warehouse`. Rather than keep appending individual words to
   `_invalid_location_value`'s keyword list (already patched once in
   CASE-001), fixed the root heuristic instead: added a
   `_BUSINESS_NAME_TERMS` check (inc/llc/corp/group/industries/packaging/
   supply/products/logistics/distribution/warehouse/... ) so any value
   containing a common business-entity word is never mistaken for a
   person's name in the first place - benefits `_invalid_location_value`
   *and* `_signature_contact_name`, which had the same latent risk.

## Decision

**ACCEPTED**
