# Operations Inbox Case Acceptance Audit — CASE-006

One Booking with Four Containers. Uses the repo's existing "Primary
Regression Example" from `docs/MULTI_CONTAINER_BOOKING_SPEC.md`
(`tests/fixtures/ricgx1235800.py` - Continental Industries Group,
RICGX1235800, 4x40HC export) rather than a new fictional example, since
it's the same real transcribed customer document already exercised by
unit tests (`test_email_parser_multi_container.py`,
`test_operations_multi_container_service.py`).

## Scope decision (read `docs/MULTI_CONTAINER_BOOKING_SPEC.md` before starting)

The booking-level pending draft (`order_intake_drafts` row) is only ever
created by a dispatcher clicking a button in
`pages_app/operations_inbox.py` - the automated
`sync_operations_email_engine` pipeline this harness drives never inserts
one. Driving the Streamlit page directly would mean building new
Streamlit-session-state test infrastructure, which is out of scope
("Do not add new Inbox functionality" / test the functions already
implemented). So this case certifies two things separately, both through
already-implemented functions:

1. **Automated intake** correctly parses `container_qty=4`/`size=40HC` and
   keeps `New Booking` classification despite passive BOL/invoice/rate-sheet
   language, via the real `sync_operations_email_engine` pipeline.
2. **`services.operations_multi_container_service.create_container_work_orders`**
   (the dispatcher-confirmed child-load-creation function - already
   implemented, previously only unit-tested at the pure-sequencing level per
   `test_operations_multi_container_service.py`'s own docstring: "no test
   database in this environment") - called directly against the real
   scratch DB, the same way CASE-001..005 simulate dispatcher order approval
   via `create_load_from_inbox_item` without driving the Streamlit page.

## Expected vs actual (field-by-field, intake level)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Export | Export | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | RICGX1235800 | RICGX1235800 | yes |
| container_count | 4 | 4 | yes |
| containers | [] (no physical numbers known yet) | [] | yes |
| customer | CONTINENTAL INDUSTRIES GROUP | (same) | yes |
| references.reference_number | SO217089a/C25749C | (same) | yes |
| references.container_size | 40HC | 40HC | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100% (intent, service_flow, customer,
  booking_number, references)
- Overall field accuracy: 100%
- Exact-record result: PASS

## Required checks (from the case spec)

- **One thread, one case, one booking-level record at intake**: confirmed -
  1 `order_intake` row, `conversation_key = email_thread_id = 'RICGX1235800'`
  (the booking number, not the message id) - matches
  `MULTI_CONTAINER_BOOKING_SPEC.md` section 7's recommendation
  (`parent_booking_key = booking_number`).
- **Quantity 4 is never read as a container number**: confirmed -
  `container_count: 4`, `containers: []`, `booking_number` stays
  `RICGX1235800` (never overwritten by the digit "4").
  `test_case_006_quantity_is_not_read_as_a_container_number` locks this in.
- **Classification stays New Booking despite BOL/invoice/rate-sheet
  language**: confirmed - the fixture body includes "OCEAN BILL OF LADING",
  "HOUSE BILL OF LADING", "THE CHARGES BELOW WILL BE INVOICED TO", "This
  Document is not an Invoice", and classification is still `New Booking`
  (`New Orders` queue) - matches
  `MULTI_CONTAINER_BOOKING_SPEC.md` section 14 and the existing
  `test_operations_classification.py` unit coverage for this exact fixture.
- **Four distinct container records, shared fields, idempotent creation**:
  confirmed via `create_container_work_orders` called directly -
  first call creates loads `[1,2,3,4]` (0 errors, `containers_created: 4`);
  second call with `containers_created=4` creates `[]` (0 errors, still 4);
  total `loads` rows for `parent_booking_key='RICGX1235800'` stays 4 after
  the second call. Each child has `container_sequence` 1-4,
  `container_total=4`, `is_placeholder_container=true`,
  `container_number=NULL` (no number invented) - matches spec sections 20-23
  exactly (`Created: 4/Existing: 0` then `Created: 0/Existing: 4`).

## Database records

- Intake: 1 `order_intake` row.
- Child-load sub-test: 4 `loads` rows created, verified idempotent on a
  second call, then cleaned up so reruns of this test file start clean.
- Duplicate rerun (intake level): row count unchanged (1 before, 1 after)
  - PASS.

## Regression test

`tests/integration/operations_inbox/test_case_006_one_booking_four_containers.py`
- `test_case_006_passes_clean`
- `test_case_006_rerun_creates_no_duplicates`
- `test_case_006_is_deterministic_across_independent_runs`
- `test_case_006_quantity_is_not_read_as_a_container_number`
- `test_case_006_child_load_creation_is_idempotent_and_creates_exactly_four`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-006 - 28 tests): 28/28 passed each time.
Full suite: 284 passed, 28 skipped - zero regressions vs. baseline.

## Git diff summary

Two real gaps found and fixed:

1. **`services/email_parser.py` - `LABEL_ALIASES["Customer"]`**: added
   `"Local Client"` - this real booking-confirmation format uses
   "LOCAL CLIENT:" instead of "Customer:", so `Customer` fell back to the
   sender's `example.com` domain guess (`"Example"`) instead of
   `"CONTINENTAL INDUSTRIES GROUP"`.
2. **`tests/integration/operations_inbox/harness.py` -
   `capture_actual_result`**: `container_count` was always
   `len(containers)`, which is wrong for a stated-quantity booking with no
   physical container numbers yet (`0`, not `4`). Now prefers the parsed
   `Container Qty` field when present, falling back to `len(containers)`
   for single-container cases (verified CASE-000..005 are unaffected, since
   none of them populate `Container Qty`).

`services/order_parser.py`'s `find_pattern` missing-comma bugs
(documented as a known limitation in CASE-005's verification.md) are
unrelated to this case's code paths (RICGX1235800 never goes through a
PDF attachment) and were not touched again here.

## Decision

**ACCEPTED**
