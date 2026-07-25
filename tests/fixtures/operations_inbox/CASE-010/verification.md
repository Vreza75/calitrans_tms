# Operations Inbox Case Acceptance Audit — CASE-010

Two Separate Orders in One Email.

## Prior status

This fixture was previously marked **NOT ACCEPTED**. The original audit
found that the pipeline lacked a real capability: the automated intake
pipeline (`_insert_operations_email_message`) always created exactly one
`order_intake` row per email message. There was no code anywhere that
detected "this message actually contains N separate bookings" and split
it into multiple drafts/rows, and no `Customer` fallback that read a
company name stated in prose ("...orders for Apex Retail.") when no
`Customer:` label existed.

A separate implementation effort (Tasks 1-4, see "Git diff summary"
below) built the missing capabilities directly in
`services/email_parser.py` and `services/operations_inbox_service.py`.
This task (Task 5) updates the certification harness to aggregate
multiple `order_intake` rows produced from one email, re-certifies the
case against the real pipeline and a disposable scratch database, fixes
one additional real defect the live run surfaced (see below), and
rewrites this audit as an acceptance.

## Additional defect found and fixed during this task

The live certification run (Step 3) initially failed on `customer`:
expected `"Apex Retail"`, actual `"Example"`. Diagnosis (per the task
brief's pointer) confirmed the `_customer_from_prose` fallback added in
Task 2 was never actually reached for this fixture: `parse_email_text`'s
sender-identity loop unconditionally copied a domain-derived
`Contact Company` (`_domain_company("nathan.brooks@example.com")` ->
`"Example"`) into `parsed["Contact Company"]`, and that value was then
copied into `parsed["Customer"]` *before* the prose fallback ever ran -
so `Customer` was never still-empty by the time
`_customer_from_prose(combined)` was reached, even though calling
`_customer_from_prose` directly against the same text correctly returns
`"Apex Retail"`.

Fixed in `services/email_parser.py`'s `parse_email_text` (this task,
production code):
- The sender-identity blind-copy loop (~line 932) now skips
  `"Contact Company"` - that field is handled explicitly afterward so a
  domain guess can't pre-empt stronger signals.
- The domain-derived `_domain_company()` fallback (weakest signal) now
  runs *after* both the signature-derived company and
  `_customer_from_prose` have had a chance, matching
  `_customer_from_prose`'s own docstring ("Only consulted when the
  label-based lookup and signature-derived company both come up empty").

This changes result ordering only for messages with no `Customer:`
label, no signature-stated company, and a business (non-free-mail,
non-own-company) sender domain - exactly CASE-010's shape. Verified no
regression: full suite before and after the fix is identical (`326
passed, 42 skipped` with `INBOX_CERTIFICATION_DATABASE_URL` unset, both
before and after), including CASE-008's already-accepted `customer:
"Example"` result (CASE-008's email has no prose "for X" phrasing, so it
still falls through to the same domain guess as before, unchanged) and
all `tests/test_order_block_splitting.py` unit tests.

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Import | Import | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | APEX-260810 | APEX-260810 | yes |
| **order_numbers** | **[APEX-260810, APEX-260811]** | **[APEX-260810, APEX-260811]** | **yes** |
| **container_count** | **2** | **2** | **yes** |
| **containers** | **[HLXU3000001, HLXU3000002]** | **[HLXU3000001, HLXU3000002]** | **yes** |
| **customer** | **Apex Retail** | **Apex Retail** | **yes** |
| pickup.terminal | Bayport Container Terminal | Bayport Container Terminal | yes |
| delivery.address | 3400 Commerce Street, Houston, TX 77002 | 3400 Commerce Street, Houston, TX 77002 | yes |
| dates.delivery_need_date | August 12, 2026 | August 12, 2026 | yes |
| references.container_size | 40HC | 40HC | yes |
| references.contact_name | Nathan Brooks | Nathan Brooks | yes |
| references.contact_email | nathan.brooks@example.com | nathan.brooks@example.com | yes |
| missing_required_fields | [] | [] | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100% (intent, order_numbers, container_count,
  containers — the case spec's declared `critical_fields`)
- Overall field accuracy: 100%
- Exact-record result: **PASS** (`exact_record_pass: True`)
- Container-count accuracy: 100%
- Container-number accuracy: 100%
- Queue resolution: PASS

Note on `pickup`/`delivery`/`dates`/`references`: the pre-implementation
`expected.json` set these to `{}`, written before any code existed and
before it was clear whether per-block field scoping would actually
capture Order 1's own `Terminal`/`Delivery Address`/`Delivery Need
Date`/`Container Size` (`capture_actual_result` reads these fields from
the *first* row only, by design - see harness.py's updated docstring).
Now that `_prepare_operations_email_records` correctly slices the email
into per-block text and reuses `_prepare_operations_email_record`
unmodified (Task 3) on each slice, block 1's fields are genuinely
present and correctly extracted - this is the email's real, explicitly
labeled content for Order 1, not an invented or bled-over value from
Order 2. Per the task brief's Step 4 guidance ("Only update
`expected.json` if... the actual result is the more correct
interpretation"), `expected.json` was corrected to match: the original
`{}` placeholders were an artifact of not knowing the implementation
yet, not a considered business requirement that this data should be
withheld.

## Required checks (from the case spec) — now met

- **Correctly identify two booking numbers**: confirmed —
  `order_numbers` holds both `APEX-260810` and `APEX-260811`, one per
  `order_intake` row's own `parsed_data["Booking Number"]`.
- **Create two distinct drafts**: confirmed — `_row_count: 2`, two
  `order_intake` rows created from one source email (see "Database
  records" below).
- **Associate each container with the correct booking**: confirmed —
  row `id=1` (`APEX-260810`) carries `HLXU3000001` in its own
  `parsed_data`; row `id=2` (`APEX-260811`) carries `HLXU3000002`; neither
  bled into the other's row. Locked in by
  `test_case_010_preserves_both_bookings_and_both_containers`.
- **Preserve shared customer information**: confirmed — both rows carry
  `Customer: "Apex Retail"` (from `_customer_from_prose`, now reachable
  after this task's ordering fix), matching the email's prose statement
  and neither the sender's raw name nor the domain-derived guess.
- **Create two orders after approval / no duplicates on rerun**:
  confirmed — `Duplicate-protection result: PASS`, row count stays at 2
  after an immediate rerun of the identical message (not 1, not 4).

## Database records

Two distinct `order_intake` rows, both linked to the same source email
via a shared `email_thread_id`:

| id | source_message_id | email_thread_id | conversation_key | request_type | work_queue |
|---|---|---|---|---|---|
| 1 | `case-010@fixtures.calitrans.test` | `case-010@fixtures.calitrans.test` | `APEX-260810` | New Booking | New Orders |
| 2 | `case-010@fixtures.calitrans.test::order-2` | `case-010@fixtures.calitrans.test` | `APEX-260811` | New Booking | New Orders |

Row 1 keeps the real base `source_message_id` (so the single
rerun-dedupe check in `sync_operations_email_engine`, keyed on that same
base id, still finds it and skips the whole email on rerun). Row 2 gets
a synthetic `::order-2` suffix to satisfy `order_intake`'s unique index
on `source_message_id`, per `_assign_split_row_identity`. The harness's
`capture_actual_result` query (`email_thread_id = :message_id or
source_message_id = :message_id`) picks up both rows for a single case.

- Duplicate rerun: row count unchanged (2 before, 2 after) —
  `Duplicate-protection result: PASS`.
- Two independent `scripts/run_inbox_case.py CASE-010` CLI runs against
  the scratch database: both `RESULT: PASSED`, both `exact_record_pass:
  True`, identical field values both times (deterministic).

## Regression test

`tests/integration/operations_inbox/test_case_010_multi_order_split.py`
- `test_case_010_passes_clean`
- `test_case_010_creates_exactly_two_rows`
- `test_case_010_rerun_creates_no_duplicates`
- `test_case_010_is_deterministic_across_independent_runs`
- `test_case_010_preserves_both_bookings_and_both_containers`
- `test_case_010_stays_in_new_orders_queue_not_review`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-010 including the 6 new CASE-010 tests — 48
tests): 48/48 passed each time (`INBOX_CERTIFICATION_DATABASE_URL` set
to the scratch database).

Full suite (`INBOX_CERTIFICATION_DATABASE_URL` unset, all certification
tests skip): `326 passed, 48 skipped` — 6 more skipped than the
pre-Task-5 baseline of 42 (this case's new certification tests, skipped
outside the opt-in scratch-DB gate). Zero failures. `python -m
compileall -q app.py pages_app services ui_components repositories
database utils ai_agents ai_core scripts tests` — clean compile.

## Git diff summary

Four capabilities were added across Tasks 1-4 (all already committed
prior to this task):

1. **`services/email_parser.py` — `detect_order_blocks(body)`**
   (commit `72b23cc`, "detect explicit multi-order block headers in an
   email body"): pure function, no DB/IO. Returns a `list[str]` of
   per-block text slices (one per detected `Order N` header, minimum 2
   headers to trigger a split, capped at 10 blocks) or `None` when no
   multi-order structure is detected.
2. **`services/email_parser.py` — `_customer_from_prose(text)`**
   (commits `4efd1f4` "recognize a customer name stated in prose when no
   label exists" and `fc9431f` "stop customer-prose regex from
   swallowing trailing prose"): fallback that reads a company name
   stated in prose ("...for Apex Retail.") when no `Customer:` label
   exists. Only intended to be consulted when the label-based lookup and
   signature-derived company both come up empty (this task fixed a gap
   in that intended precedence - see above).
3. **`services/operations_inbox_service.py` —
   `_prepare_operations_email_records(message)`**
   (commit `87d0131`, "prepare one record per detected order block"):
   per-block orchestration - calls `detect_order_blocks` on the message
   body and, when it returns multiple blocks, runs the existing
   `_prepare_operations_email_record` unmodified once per block instead
   of once for the whole message, returning `list[dict]` instead of a
   single `dict`.
4. **`services/operations_inbox_service.py` —
   `_assign_split_row_identity(records, base_message_id)`** and the
   insert-loop refactor
   (commit `7c11e1b`, "insert one order_intake row per detected order
   block"): assigns row-identity fields (`message_id`, `thread_id`) when
   more than one record was produced - block 0 keeps the real
   `base_message_id`, blocks 1+ get a synthetic `::order-N` suffix to
   satisfy `order_intake`'s unique index on `source_message_id`, while
   every record's `thread_id` is forced to `base_message_id` so a query
   for "all rows from this email" is `email_thread_id = base_message_id`.
   `_insert_operations_email_message`'s single-record insert became a
   loop over `_assign_split_row_identity(records, base_message_id)`.

Task 5 (this task) changes the certification harness and fixture, and
one production defect surfaced by certifying against the real pipeline:

5. **`tests/integration/operations_inbox/harness.py` —
   `capture_actual_result`**: now queries `order_intake` by
   `email_thread_id = :message_id or source_message_id = :message_id`
   instead of `source_message_id` alone, so it picks up every row from a
   split email. `order_numbers`/`containers`/`container_count` are now
   aggregated across every row (`booking_number` = first row's booking
   number, for backward compatibility with single-row cases);
   `requires_human_review` now uses `any(...)` across all rows instead of
   reading only the first row's flag. Provably unchanged for every
   existing single-row case (rows has exactly one element in every prior
   case, so all aggregations degenerate to the original single-row
   values).
6. **`services/email_parser.py` — `parse_email_text`** (this task,
   production code): fixed the `Customer` fallback precedence bug
   described above - domain-derived `Contact Company` no longer
   pre-empts the prose fallback for messages with a business sender
   domain and no signature-stated company.
7. **`tests/fixtures/operations_inbox/CASE-010/expected.json`**:
   `pickup`/`delivery`/`dates`/`references` corrected from the
   pre-implementation `{}` placeholders to Order 1's actual, correctly
   parsed field values (see note under "Expected vs actual" above).
   `order_numbers`, `container_count`, `containers`, `customer`, `queue`,
   `decision`, and `requires_human_review` were already correct in the
   pre-existing `expected.json` (written from the business requirement)
   and needed no change.

## Known limitation

The per-block insert loop
(`_insert_operations_email_message` -> `_assign_split_row_identity` ->
one insert per record) is **not transactional across blocks**. If a
later block's insert fails after an earlier block's insert already
committed, the earlier block's row is permanently stranded: a rerun of
the same message is skipped entirely by the existing single rerun-dedupe
check in `sync_operations_email_engine` (keyed on the base
`source_message_id`, which the earlier block already claimed), so the
missing later block(s) can never be retried automatically - only a
manual dispatcher/DB intervention would recover it.

This gap was identified during Task 4's review and the user was asked
directly; the decision was to defer fixing it rather than adding
cross-block transactional rollback now. This matches the existing shape
of `services/operations_multi_container_service.py`'s
`create_container_work_orders()` in this codebase, which is also
idempotent-by-check (safe to retry, no duplicates) rather than
transactional (no guaranteed all-or-nothing). Not fixed here - flagging
for a separate scoping conversation if CASE-010-shaped emails turn out
to fail mid-split often enough in production to matter.

## Decision

**ACCEPTED**
