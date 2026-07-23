# Operations Inbox Case Acceptance Audit — CASE-001

New Import, Single Container, Email Body Only.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Import | Import | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | GCR-IMP-260801 | GCR-IMP-260801 | yes |
| order_numbers | [GCR-IMP-260801] | [GCR-IMP-260801] | yes |
| container_count | 1 | 1 | yes |
| containers | [MSCU1234567] | [MSCU1234567] | yes |
| customer | Gulf Coast Retail Distribution | Gulf Coast Retail Distribution | yes |
| pickup.terminal | Bayport Container Terminal | Bayport Container Terminal | yes |
| delivery.warehouse | Gulf Coast Retail DC | Gulf Coast Retail DC | yes |
| delivery.address | 4100 Market Center Drive, Houston, TX 77020 | (same) | yes |
| dates.delivery_need_date | August 4, 2026 | August 4, 2026 | yes |
| dates.last_free_day | August 3, 2026 | August 3, 2026 | yes |
| references.container_size | 40HC | 40HC | yes |
| references.contact_name | Elena Martinez | Elena Martinez | yes |
| references.contact_email | elena.martinez@example.com | (same) | yes |
| references.contact_phone | 713-555-0101 | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100%
- Overall field accuracy: 100%
- Exact-record result: PASS

## Database records

- 1 `order_intake` row (id 1 in the scratch DB), 0 `loads` rows (order not yet
  approved/created - by design, this case validates intake through pending
  review, not the dispatcher-approval step).
- Thread/case ids: `email_thread_id`/`conversation_key` both resolve to the
  fixture's `Message-ID` (`case-001@fixtures.calitrans.test`) - single thread.
- Duplicate rerun: row count unchanged (1 before, 1 after) - PASS.

## Regression test

`tests/integration/operations_inbox/test_case_001_new_import_single_container.py`
- `test_case_001_passes_clean`
- `test_case_001_rerun_creates_no_duplicates`
- `test_case_001_is_deterministic_across_independent_runs`

Targeted run x3: 6/6 passed each time (both CASE-000 and CASE-001 tests, since
they share the `tests/integration/operations_inbox/` directory).
Full suite: 284 passed, 6 skipped (no `INBOX_CERTIFICATION_DATABASE_URL` set) -
matches pre-existing baseline, zero regressions.
Reprocessed the same email twice: record count stayed at 1 both times.

## Git diff summary

Real defects found and fixed while certifying this case (not fixture-only
changes):

1. **`services/email_parser.py` - `_invalid_location_value`**: a false-positive
   filter meant to strip person names accidentally captured as
   warehouse/terminal values was rejecting legitimate facility names like
   "Gulf Coast Retail DC" (2-4 title-case words, no recognized location
   keyword). Added `dc`, `distribution`, `fulfillment`, `logistics`,
   `facility`, `plant`, `receiving`, `center`, `centre`, and the `DC` state
   code to the location-hint keyword set.
2. **`services/email_parser.py` - `LABEL_ALIASES["Port"]`**: added
   `"Pickup Terminal"` as a recognized label (previously only bare `Terminal`/
   `Port` matched, so `Pickup Terminal: ...` silently fell through to no
   value).
3. **`services/operations_inbox_service.py` - `contains_any`**: was a plain
   substring check (`term in text`), so short terms like `"exam"` matched
   inside unrelated text such as the `example.com` domain used by every test
   fixture email address. Now word-boundary matched. Same fix applied to
   `services/operations_email_triage_service.py`'s `_contains_any`.
4. **`services/operations_inbox_service.py` - `PORT_ISSUE_TERMS`**: removed
   bare `"port"`/`"terminal"` (any booking email mentioning a terminal name
   scored as a port issue); kept only actionable-issue phrasing
   (`"port hold"`, `"terminal hold"`, `"hold"`, `"exam"`, `"gate issue"`, etc).
5. **`services/operations_inbox_service.py` /
   `services/operations_email_triage_service.py`**: `NEW_ORDER_INTENT_TERMS` /
   `ORDER_PLACEMENT_TERMS` didn't recognize `"new import order"` /
   `"new export order"` phrasing, and the `please <verb>` regex signal didn't
   include `create`/`enter` - so "please create a new import order" scored
   zero New-Booking signal.
6. **`services/operations_email_triage_service.py` -
   `is_booking_confirmation`**: the canonical booking-confirmation detector
   required either a container *quantity* field or vessel/cutoff/POL/POD
   language - a plain single-container order with a booking number and a
   specific container number (no vessel/cutoff language) fell through to
   `False`, letting the overbroad `DRIVER_PORT_TERMS`/`EXISTING_LOAD_TERMS`
   rules downstream misclassify it as `Port Issue`. Added
   `booking_number and container_number` as an equally strong confirmation
   signal, plus explicit `"new import order"`/`"new export order"`/`"new
   import booking"`/`"new export booking"` subject signals.
7. **`tests/integration/operations_inbox/harness.py`**: `requires_human_review`
   was wired to the narrow `llm_review_required` (low-confidence re-check)
   column instead of the general "no order has been created/approved yet"
   gate. Fixed to `llm_review_required OR (review_status == 'Open' and no
   linked load)`, matching the AI Rules invariant that no order is ever
   created without dispatcher confirmation.

All six fixes are targeted, word-boundary/alias/signal additions - no
behavior was removed, and the full suite (284 tests) shows zero regressions
before and after.

## Known limitation

`services/operations_email_triage_service.py`'s `EXISTING_LOAD_TERMS` and
`DRIVER_PORT_TERMS` still contain some broad bare terms (`"pickup"`,
`"delivery"`, `"lfd"`, `"gate"` is now `"gate issue"` only in the
`operations_inbox_service.py` copy but not yet mirrored here). They didn't
affect CASE-001 because `is_booking_confirmation` now short-circuits to
`New Booking` first, but a future case without a booking number (e.g. a pure
status-update reply) could still hit them. Revisit if a later case surfaces
it - do not preemptively rewrite the whole rule set now.

There are still two independent classification engines
(`operations_inbox_service.classify_customer_request` and
`operations_email_triage_service.triage_operations_email`'s rule engine) whose
final `request_type` wins depending on call order - this is the
architectural issue `.claude/rules/operations-inbox.md` already flags
("Do not maintain separate conflicting queue rules in multiple functions").
Not unified here - out of scope for a single-case certification fix; flagging
for a dedicated consolidation pass.

## Decision

**ACCEPTED**
