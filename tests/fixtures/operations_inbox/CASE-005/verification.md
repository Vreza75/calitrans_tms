# Operations Inbox Case Acceptance Audit — CASE-005

New Import with PDF Attachment. PDF fixture was hand-built (no PDF-writer
library like reportlab/fpdf is installed - not added as a new dependency
for one test fixture); verified round-trip readable via `pdfplumber`
before being wired into the case (`tests/fixtures/operations_inbox/CASE-005/attachments/coastal_appliance_booking.pdf`).

## Expected vs actual (field-by-field)

| Field | Expected | Actual | Match |
|---|---|---|---|
| intent | New Booking | New Booking | yes |
| service_flow | Import | Import | yes |
| queue | New Orders | New Orders | yes |
| decision | Create New Order | Create New Order | yes |
| existing_load_match | null | null | yes |
| booking_number | CAG-IMP-260805 | CAG-IMP-260805 | yes |
| container_count | 1 | 1 | yes |
| containers | [CMAU1122334] | [CMAU1122334] | yes |
| customer | Coastal Appliance Group | Coastal Appliance Group | yes |
| pickup.terminal | Bayport Container Terminal | (same) | yes |
| delivery.warehouse | Coastal Appliance Receiving | (same) | yes |
| delivery.address | 6300 East Sam Houston Parkway North, Houston, TX 77049 | (same) | yes |
| dates.delivery_need_date | August 8, 2026 | August 8, 2026 | yes |
| dates.last_free_day | August 7, 2026 | August 7, 2026 | yes |
| references.container_size | 40HC | 40HC | yes |
| references.contact_name | Dana Phillips (email sender, not the PDF) | Dana Phillips | yes |
| references.contact_email | dana.phillips@example.com | (same) | yes |
| requires_human_review | true | true | yes |

- Critical-field accuracy: 100% (intent, service_flow, customer,
  booking_number, containers, delivery, dates)
- Overall field accuracy: 100%
- Exact-record result: PASS (after three real defects were fixed - see Git
  diff summary; this case did **not** pass on the first run)

## Required validation (from the case spec)

- **Attachment is stored**: confirmed - `coastal_appliance_booking.pdf`
  written to `storage/load_documents/operations_inbox/`
  (`test_case_005_attachment_is_physically_stored`).
- **PDF text is extracted**: confirmed - `pdfplumber` extraction round-trip
  verified manually before wiring the fixture; `extract_text_from_pdf` +
  `parse_order_text` produced 11/11 real fields, rule-parser confidence
  0.95 (no LLM fallback call - fully deterministic, no network access).
- **Critical fields linked to the attachment source**: confirmed - see the
  `force=True` attachment-merge fix below; `booking_number`/`customer`
  come from the PDF, not the short email body.
- **Email and attachment remain in one thread**: confirmed -
  `order_intake.email_thread_id = 'CAG-IMP-260805'` (the booking number
  found in the PDF), one row for the one message.
- **One pending draft created**: confirmed - 1 `order_intake` row, 0
  `loads` rows (pending dispatcher approval).
- **Reprocessing does not duplicate the attachment or order**: confirmed -
  row count unchanged after reprocessing (1 before, 1 after).

## Database records

- 1 `order_intake` row, 0 `loads` rows.
- Duplicate rerun: row count unchanged (1 before, 1 after) - PASS.
- Two independent full CLI runs: deterministic, both PASSED.

## Regression test

`tests/integration/operations_inbox/test_case_005_import_with_pdf_attachment.py`
- `test_case_005_passes_clean`
- `test_case_005_rerun_creates_no_duplicates`
- `test_case_005_is_deterministic_across_independent_runs`
- `test_case_005_pdf_fields_win_over_weak_email_body_guesses`
- `test_case_005_attachment_is_physically_stored`

Targeted run x3 (whole `tests/integration/operations_inbox/` directory,
CASE-000 through CASE-005 - 23 tests): 23/23 passed each time.
Full suite: 284 passed, 23 skipped - zero regressions vs. baseline.

## Git diff summary

Four real defects found and fixed, all discovered by direct inspection of
intermediate parser output before/alongside live pipeline runs (not
guessed):

1. **`services/email_parser.py` - Booking Number subject fallback regex**:
   `\bbooking...\b[^A-Z0-9]{0,20}([A-Z0-9][A-Z0-9-]{4,})\b` matched
   case-insensitively, so an ordinary word after "Booking" in a subject
   (e.g. "...Attached Booking Document") was captured as `"DOCUMENT"`.
   Added a digit-presence guard - a real booking number is always an
   alphanumeric code, never a plain word.
2. **`services/operations_attachment_service.py` -
   `merge_saved_attachment_fields`**: default `force=False` meant a field
   already populated by the weak email-body-only parse (e.g. `Customer`
   inferred as `"Example"` from the sender's `example.com` domain) blocked
   the PDF's correct, explicitly-labeled value from ever being applied.
   Per the documented parsing precedence (specialized document parser >
   generic email-body parser), the initial merge in
   `_prepare_operations_email_record` now calls with `force=True`.
3. **Same function - identity-field carve-out**: `force=True` alone let
   the PDF's own flawed signature-scan overwrite the *correct*,
   sender-header-derived `Contact Name` (`"Dana Phillips"`) with a garbage
   value (`"Steamship Line: CMA CGM"` - the PDF has no real signature
   block for `_signature_contact_name` to find, so it misfired on an
   unrelated line). Added `_ATTACHMENT_MERGE_IDENTITY_FIELDS` so Contact
   Name/Email/Phone/Company always stay fill-blank-only regardless of
   `force` - sender identity is a stronger signal than a document-text
   scan for who is asking.
4. **`services/order_parser.py` - `Size` regex**: only captured the
   numeric prefix (`"40"`), dropping the `HC`/`FT` suffix, because the
   equipment-type group was non-capturing. Widened the capture group to
   include the suffix.

Also reverted two comma additions to `services/order_parser.py`'s
`Customer`/`Port`/`Warehouse` `find_pattern` lists after discovering they
interact badly with `find_pattern`'s `re.DOTALL` flag - a `.+`-style
pattern with a real comma greedily swallows the rest of the multi-line
document text instead of stopping at the label's own line. Left as
missing-comma string-concatenation (effectively an inert combined
pattern that never matches) since the existing generic
`parse_email_text` fallback already resolves these fields correctly, and
the DOTALL interaction needs a more careful fix than a same-session
one-liner.

## Known limitation

Reference-number extraction, PDF-vs-body field provenance tagging (e.g.
storing which parser/attachment a field came from, as
`.claude/rules/operations-inbox.md` recommends), and the
`Customer`/`Port`/`Warehouse` `find_pattern` comma bugs in
`services/order_parser.py` remain - none of them block this case's
required validations, and the comma bugs are currently harmless because
`parse_email_text`'s fallback already produces the correct values.

## Harness change

Added a hard guard in `tests/integration/operations_inbox/harness.py`'s
`load_fixture()`: an empty or missing `expected.json` now raises instead
of silently producing a vacuous 100%-accuracy pass (caught during this
case - the very first CASE-005 run "passed" before `expected.json` had
been written, because `compare()` has nothing to compare against an empty
dict).

## Decision

**ACCEPTED**
