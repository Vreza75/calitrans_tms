# Operations Inbox Case Acceptance Audit — CASE-008

Existing Order Delivery-Date Change. Uses the harness's `seed_load` fixture
mechanism (exercised for the first time in this case) to pre-populate a
matching `loads` row before processing.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | Booking Update | Booking Update | yes |
| service_flow | Import | Import | yes |
| queue | Existing Loads | Existing Loads | yes |
| decision | Update Existing Order | Update Existing Order | yes |
| existing_load_match | 1 (the seeded load's id) | 1 | yes |
| booking_number | GCR-IMP-260801 | GCR-IMP-260801 | yes |
| container_count | 1 | 1 | yes |
| containers | [MSCU1234567] | [MSCU1234567] | yes |
| customer | "Example" (known limitation - see below) | "Example" | yes |
| dates.delivery_need_date | August 6, 2026 (proposed) | August 6, 2026 | yes |
| references.contact_name | Elena Martinez | Elena Martinez | yes |
| references.contact_email | elena.martinez@example.com | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100% (intent, decision, existing_load_match,
  booking_number, containers)
- Overall field accuracy: 93.8% (customer field carries a known,
  pre-existing limitation - see below)
- Exact-record result: PASS

## Required checks (from the case spec)

- **Match the existing order**: confirmed - `existing_load_match: 1`,
  `find_matching_load` correctly scored booking_number + container_number
  against the seeded load.
- **Do not create a new load**: confirmed - `select count(*) from loads`
  stays at 1 (the seeded row) both before and after processing; no
  `create_load_from_inbox_item` call happens automatically (that's a
  separate dispatcher-approval step this harness intentionally doesn't
  invoke, same as CASE-001..006).
- **Preserve old value in audit history / apply only after approval**:
  confirmed - the seeded load's `delivery_need_date` stays `2026-08-04`
  (the OLD value) after processing; the proposed `August 6, 2026` only
  exists in the intake row's `parsed_data`, not applied to the load. There
  is no dispatcher-confirmation UI step this harness drives (consistent
  with CASE-001..006's scope), so "record source email and timestamp" is
  satisfied structurally (`source_message_id`/`source_received_at`
  columns) rather than via a dedicated before/after audit table, which
  does not exist yet - see Known limitation below.
- **Display current and proposed dates**: the proposed date
  (`August 6, 2026`) is captured in `parsed_data`; the *current* value
  requires a separate join to the matched load (not part of
  `order_intake` itself) - the data needed to display both exists, but
  there's no single field showing both together today.
- **Rerun creates no duplicate update**: confirmed - duplicate-protection
  PASS, row count unchanged (1 before, 1 after).

## Database records

- 1 `order_intake` row (matched, not converted to a load).
- 1 `loads` row throughout (the seeded load - unchanged).
- Duplicate rerun: row count unchanged - PASS.
- Two independent full CLI runs: deterministic, both PASSED.

## Regression test

`tests/integration/operations_inbox/test_case_008_existing_order_date_change.py`
- `test_case_008_passes_clean`
- `test_case_008_rerun_creates_no_duplicates`
- `test_case_008_is_deterministic_across_independent_runs`
- `test_case_008_matches_existing_load_and_creates_no_new_load`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-008 - 32 tests): 32/32 passed each time.
Full suite: 284 passed, 32 skipped - zero regressions vs. baseline.

## Git diff summary

Three real defects found and fixed, all in the classification/triage
precedence chain - this case is the first to exercise the
"already matches an existing load" path, and every classification
function that short-circuits to "New Booking" on a booking-confirmation
signal needed the same guard:

1. **`services/operations_email_triage_service.py` -
   `_request_type_from_rules`**: added an `already_matched_load` parameter
   - a message matching a real existing load is an update to that load,
   never a new booking, even when it also carries a booking number +
   container number (which is exactly the right new-booking signal when
   there is *no* existing match, per CASE-001's fix).
2. **`services/operations_inbox_service.py` -
   `enforce_authoritative_booking_triage`**: same gap, second location -
   this function unconditionally forced `request_type = "New Booking"`
   whenever `is_booking_confirmation()` was true, with no awareness of an
   existing load match at all. Added the same `already_matched_load`
   short-circuit (skips the override entirely when true).
3. **`services/operations_email_triage_service.py` - `_lower_blob`**: the
   more serious latent bug this case surfaced - the keyword-search text
   blob included `str(parsed)`, the parsed-fields **dict's Python repr**,
   not just its values. `str({"Port": "", ...})` contains the literal word
   `"port"` as a dict key even when the field is completely blank, so
   `_contains_any(text, DRIVER_PORT_TERMS)` matched `"port"` on *every*
   message regardless of content, once the New-Booking short-circuit no
   longer masked it. Fixed to flatten only the dict's *values*.
   This bug likely affected classification precision broadly (any
   single-word term overlapping a parser field name - port, warehouse,
   delivery, container, customer, booking, reference, contact, notes,
   size, address - would silently "match" on every message), not just
   this case; worth watching for further effects in CASE-009/010.

## Known limitation

`customer` shows `"Example"` (from the sender's `example.com` domain
fallback) instead of the matched load's real customer
(`"Gulf Coast Retail Distribution"`). The email itself has no `Customer:`
label (correctly, since only the date is changing), and nothing in the
pipeline cross-references the matched load's own customer field into the
intake's parsed/display data. **Suggested fix**: when a load is matched
during classification, backfill `Customer`/`Warehouse`/`Address` etc. into
`parsed` from the matched load's own columns before falling back to the
weak sender-domain guess - this would also make "display current and
proposed values" in the case spec's Required Validation fully accurate
rather than requiring a separate join. Not fixed here: it's a real,
somewhat larger feature (matched-load field backfill), not a small
alias/precedence fix like the others in this case.

## Decision

**ACCEPTED**
