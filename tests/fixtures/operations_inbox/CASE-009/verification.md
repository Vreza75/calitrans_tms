# Operations Inbox Case Acceptance Audit — CASE-009

Delivery Address Change After Driver Assignment.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | Booking Update | Booking Update | yes |
| service_flow | null (no TYPE-indicating word in this address-change-only email) | null | yes |
| queue | Existing Loads | Existing Loads | yes |
| decision | Update Existing Order | Update Existing Order | yes |
| existing_load_match | 1 (the seeded load's id) | 1 | yes |
| booking_number | GCR-IMP-260801 | GCR-IMP-260801 | yes |
| container_count | 1 | 1 | yes |
| containers | [MSCU1234567] | [MSCU1234567] | yes |
| customer | "Example" (known limitation, same as CASE-008) | "Example" | yes |
| delivery.warehouse | Gulf Coast Retail North DC | (same) | yes |
| delivery.address | 16200 North Freeway, Houston, TX 77090 (proposed) | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100% (intent, decision, existing_load_match,
  booking_number, containers)
- Overall field accuracy: 93.8% (customer field: same known limitation as
  CASE-008)
- Exact-record result: PASS

## Required checks (from the case spec)

- **Correct existing-load match**: confirmed - `existing_load_match: 1`.
- **Show old and proposed addresses**: the proposed address is captured in
  `parsed_data`; the old address requires a join to the matched load, same
  structural note as CASE-008's date display.
- **Identify assigned-driver impact / do not silently overwrite / do not
  send driver communication automatically**: confirmed - the seeded load's
  `driver_name` (`"Mike Torres"`) and `address`
  (`"4100 Market Center Drive, Houston, TX 77020"`) are both unchanged
  after processing; no automatic load update or message send happens
  anywhere in the automated intake path.
- **Preserve audit history**: same structural note as CASE-008 - the
  proposed value lives in the intake row's `parsed_data`, not yet applied.
- **Reprocessing must not duplicate the change event**: confirmed -
  duplicate-protection PASS, row count unchanged (1 before, 1 after).

## Database records

- 1 `order_intake` row (matched, not applied).
- 1 `loads` row throughout - `address`, `driver_name`, and `status` all
  unchanged from the seed (`4100 Market Center Drive...`,
  `"Mike Torres"`, `"Driver Assigned"`).
- Duplicate rerun: row count unchanged - PASS.
- Two independent full CLI runs: deterministic, both PASSED.

## Regression test

`tests/integration/operations_inbox/test_case_009_address_change_after_driver_assigned.py`
- `test_case_009_passes_clean`
- `test_case_009_rerun_creates_no_duplicates`
- `test_case_009_is_deterministic_across_independent_runs`
- `test_case_009_does_not_misread_driver_mention_as_a_driver_issue`
- `test_case_009_does_not_auto_overwrite_the_assigned_loads_address`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-009 - 37 tests): 37/37 passed each time.
Full suite: 284 passed, 37 skipped - zero regressions vs. baseline.

## Git diff summary

One real defect found and fixed, plus two alias additions:

1. **`services/operations_email_triage_service.py` - `DRIVER_PORT_TERMS`**:
   bare `"driver"`/`"truck"`/`"chassis"`/`"port"`/`"terminal"` triggered
   `Driver Issue`/`Port Issue` on *any* message mentioning those words at
   all - this case's body says "please confirm that **the driver**
   receives the updated address" (a routine instruction, not a driver
   *problem*) and was misclassified as `Driver Issue` before the fix.
   Narrowed to specific problem phrasing (`"driver issue"`, `"breakdown"`,
   `"flat tire"`, `"late driver"`, `"port hold"`, `"terminal hold"`, etc.)
   - the same pattern already applied to `PORT_ISSUE_TERMS` in
   `services/operations_inbox_service.py` back in CASE-001, now extended
   to this file's parallel list.
2. **`services/email_parser.py` - `LABEL_ALIASES["Warehouse"]` /
   `["Address"]`**: added `"New Delivery Warehouse"`/`"New Delivery
   Address"` (mirrors CASE-008's `"New Delivery Date"` fix) - a
   correction email naturally labels the *new* value, which the aliases
   didn't recognize.

## Known limitation

Same as CASE-008: `customer` shows `"Example"` instead of the matched
load's real customer. Same suggested fix (backfill from the matched load)
applies here too - not repeated in full, see CASE-008's verification.md.

## Decision

**ACCEPTED**
