
# CaliTrans TMS Test Matrix

## 1. Purpose

This document defines the minimum tests required to safely review, refactor, and improve the CaliTrans TMS.

The tests are intended to protect:

- Operations Inbox behavior
- Email synchronization
- Email classification
- Booking parsing
- Pending order drafts
- Conversation history
- Multi-container booking creation
- Existing-load matching
- Customer replies
- Database integrity
- Streamlit behavior
- Local and cloud deployment

Claude should add or update automated tests before making risky changes.

---

## 2. Testing Principles

All important business behavior should have a repeatable test.

Tests should:

- Use deterministic fixtures
- Avoid calling production email accounts
- Avoid modifying production data
- Avoid sending real customer emails
- Avoid using real secrets
- Mock external services where practical
- Verify database side effects
- Verify duplicate protection
- Verify failure behavior
- Verify dispatcher-confirmed values are preserved

Do not claim that a feature is complete only because the code compiles.

---

## 3. Standard Verification Commands

Run the full compile check:

```powershell
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core
````

Run all tests:

```powershell
pytest -q
```

Run tests with more detail:

```powershell
pytest -v
```

Run one test file:

```powershell
pytest tests\test_operations_classification.py -q
```

Run one test by name:

```powershell
pytest tests\test_operations_classification.py -k booking_confirmation -q
```

Run the application manually:

```powershell
python -m streamlit run app.py
```

Use actual test filenames found in the repository. Create missing test files where needed.

---

# 4. Compile and Import Tests

| Test                             | Expected result                                |
| -------------------------------- | ---------------------------------------------- |
| Compile `app.py`                 | No syntax errors                               |
| Compile `pages_app/`             | No syntax errors                               |
| Compile `services/`              | No syntax errors                               |
| Compile `repositories/`          | No syntax errors                               |
| Compile `database/`              | No syntax errors                               |
| Import Operations Inbox page     | No import errors                               |
| Import Operations Inbox services | No circular-import errors                      |
| Import repositories              | No database connection attempted during import |
| Import parser modules            | No missing dependency errors                   |

Required result:

```text
No SyntaxError
No ImportError
No circular import
```

---

# 5. Classification Test Matrix

The application must use one canonical classification result.

## 5.1 New Booking Tests

| Input                                                | Expected queue | Expected request type |
| ---------------------------------------------------- | -------------- | --------------------- |
| New booking confirmation                             | New Orders     | New Booking           |
| Booking confirmation with attached PDF               | New Orders     | New Booking           |
| Booking containing “bill of lading”                  | New Orders     | New Booking           |
| Booking containing “invoice contact”                 | New Orders     | New Booking           |
| Booking containing “charges will be invoiced”        | New Orders     | New Booking           |
| Booking containing a rate sheet                      | New Orders     | New Booking           |
| Booking containing “this document is not an invoice” | New Orders     | New Booking           |
| Booking number plus container quantity               | New Orders     | New Booking           |
| Booking confirmation in Spanish                      | New Orders     | New Booking           |

A clear booking confirmation must override passive billing terminology.

## 5.2 Billing Tests

| Input                                | Expected queue | Expected request type |
| ------------------------------------ | -------------- | --------------------- |
| “Please send invoice 123”            | Billing        | Billing Request       |
| “Please correct invoice 123”         | Billing        | Invoice Correction    |
| “We have not received the invoice”   | Billing        | Billing Request       |
| “The detention invoice is incorrect” | Billing        | Billing Dispute       |
| “Please confirm payment status”      | Billing        | Payment Question      |
| “Please invoice this completed load” | Billing        | Billing Request       |

Billing requires an actionable request.

## 5.3 Quote Tests

| Input                                       | Expected queue | Expected request type |
| ------------------------------------------- | -------------- | --------------------- |
| “Please quote this shipment”                | Quotes         | Quote Request         |
| “What is your rate from Bayport to Dallas?” | Quotes         | Quote Request         |
| Spanish rate request                        | Quotes         | Quote Request         |

## 5.4 Appointment and PIN Tests

| Input                       | Expected queue     |
| --------------------------- | ------------------ |
| Appointment request         | Appointments / PIN |
| Appointment confirmation    | Appointments / PIN |
| Terminal PIN request        | Appointments / PIN |
| Pickup number request       | Appointments / PIN |
| Delivery appointment change | Appointments / PIN |

## 5.5 Existing Load Update Tests

| Input                                  | Expected queue        |
| -------------------------------------- | --------------------- |
| Gate changed for known booking         | Existing Load Updates |
| Delivery date changed                  | Existing Load Updates |
| Container assigned to existing booking | Existing Load Updates |
| Customer cancels known load            | Existing Load Updates |
| Driver-status request for known load   | Existing Load Updates |

## 5.6 Documents Tests

| Input                    | Expected queue |
| ------------------------ | -------------- |
| Please send POD          | Documents      |
| Missing delivery receipt | Documents      |
| Request for signed BOL   | Documents      |
| Document-only follow-up  | Documents      |

## 5.7 Archive Tests

| Input                          | Expected result      |
| ------------------------------ | -------------------- |
| Marketing email                | Store Only / Archive |
| LinkedIn notification          | Store Only / Archive |
| Newsletter                     | Store Only / Archive |
| No-action informational notice | Store Only / Archive |
| Spam                           | Store Only / Archive |

---

# 6. RICGX1235800 Regression Fixture

Use this booking as a permanent regression fixture.

Expected source values:

```text
Customer: Continental Industries Group
Booking Number: RICGX1235800
Reference Number: SO217089a/C25749C
Service Flow: Export
Containers Required: 4
Container Size: 40HC
Known Container Numbers: None
Commodity: Resin Non Haz
Warehouse: PBP Packaging
Trucker: CaliTrans
Empty Pickup: ConGlobal-La Porte
Full Return Terminal: Bayport Terminal
Carrier: ONE
Vessel: CONTI CORTESIA 013W
Port of Loading: Houston
Port of Discharge: Kaohsiung
Documentation Cutoff: 2026-07-22 12:00
VGM Cutoff: 2026-07-24 12:00
Cargo Cutoff: 2026-07-24 16:00
Sailing Date: 2026-07-27
ETA Date: 2026-08-31
```

Expected classification:

```text
Queue: New Orders
Request Type: New Booking
Department: Dispatch
Service Flow: Export
```

Must not classify as:

```text
Billing
Documents
Existing Load Updates
```

unless a later message contains a real actionable request for one of those queues.

---

# 7. Email Normalization Tests

## 7.1 Plain Text

Verify:

* Plain-text body is preserved.
* Line breaks remain usable.
* The latest customer-authored section can be identified.
* Signatures do not contaminate operational fields.

## 7.2 HTML

Verify:

* Raw HTML is preserved.
* Human-readable text is extracted.
* HTML entities are decoded.
* Hidden or decorative content does not become field data.

## 7.3 HTML Table

Given an HTML table containing:

```text
RELEASE REF: SO217089a/C25749C - RICGX1235800
CONTAINER QTY: 4 X40
BAG TYPE: 25 KG BAGS
VOLUME (MT): 99
TOTAL BAG: 3960
GRADE: HDPE HD5207F
FFW: FLEUR DE LIS
WAREHOUSE: PBP PACKAGING
TRUCKER: CALI TRANS
BOOKING: RICGX1235800
DOC CUT OFF: 7/22
PORT CUTOFF: 7/24
```

Expected normalized output must retain each label and value separately.

The parser must not produce:

```text
Warehouse = PBP PACKAGING TRUCKER: CALI TRANS
```

or:

```text
Reference = SO217089a/C25749C CONTAINER QTY: 4 X40...
```

---

# 8. Booking Parser Tests

For the RICGX1235800 fixture, assert:

```text
Customer = Continental Industries Group
Booking Number = RICGX1235800
Reference Number = SO217089a/C25749C
Container Qty = 4
Container Size = 40HC
Service Flow = Export
Commodity = Resin Non Haz
Warehouse = PBP Packaging
Trucker = CaliTrans
Empty Pickup = ConGlobal-La Porte
Full Return Terminal = Bayport Terminal
Carrier = ONE
Vessel = CONTI CORTESIA 013W
Port of Loading = Houston
Port of Discharge = Kaohsiung
Documentation Cutoff = 2026-07-22 12:00
VGM Cutoff = 2026-07-24 12:00
Cargo Cutoff = 2026-07-24 16:00
Sailing Date = 2026-07-27
ETA Date = 2026-08-31
```

The parser must not produce:

```text
Container Qty = 1
Container Size = 40
Service Flow = 25 KG BAGS
Container Number = RICGX1235800
Warehouse = PBP PACKAGING TRUCKER: CALI TRANS
```

---

# 9. Container Quantity Tests

| Source text                           | Expected quantity | Expected size                     |
| ------------------------------------- | ----------------: | --------------------------------- |
| `4 X40` with PDF confirming high cube |                 4 | 40HC                              |
| `4 X 40HC`                            |                 4 | 40HC                              |
| `4X40HQ`                              |                 4 | 40HC                              |
| `NUMBER OF CNTRS: 4 X 40HC`           |                 4 | 40HC                              |
| `2 containers, 20 foot`               |                 2 | 20                                |
| Missing quantity                      |              NULL | Parsed separately or Needs Review |
| Invalid quantity `0`                  |      NULL / error | Needs Review                      |
| Invalid quantity `-1`                 |      NULL / error | Needs Review                      |

Never default unknown quantity to `1`.

---

# 10. Container Size Normalization Tests

| Source value                    | Expected normalized value |
| ------------------------------- | ------------------------- |
| `40HC`                          | `40HC`                    |
| `40 HQ`                         | `40HC`                    |
| `40HQ`                          | `40HC`                    |
| `40 High Cube`                  | `40HC`                    |
| `20GP`                          | `20`                      |
| `20DV`                          | `20`                      |
| `45HQ`                          | `45HC`                    |
| `40` without supporting context | Needs Review              |
| Blank                           | NULL / Needs Review       |

---

# 11. Field Validation Tests

Reject values containing embedded unrelated labels.

## Invalid Reference Example

```text
SO217089a/C25749C CONTAINER QTY: 4 X40 BAG TYPE: 25 KG BAGS
```

Expected:

```text
Reference value rejected or trimmed correctly
Parse error recorded
Draft marked for review if unresolved
```

## Invalid Warehouse Example

```text
PBP PACKAGING TRUCKER: CALI TRANS
```

Expected:

```text
Warehouse = PBP PACKAGING
Trucker = CALI TRANS
```

## Invalid Service Flow Example

```text
25 KG BAGS 0.025 VOLUME
```

Expected:

```text
Service Flow = NULL or Needs Review
```

Valid service flows are only:

```text
Import
Export
Local Import
Local Export
```

---

# 12. Parser Source-Authority Tests

Use this precedence:

```text
Dispatcher-confirmed
> specialized document parser
> structured email-table parser
> validated generic parser
> LLM suggestion
```

Required tests:

1. Specialized parser value replaces generic parser value.
2. Email-table value replaces an LLM suggestion.
3. Dispatcher-confirmed value cannot be overwritten by any parser.
4. Blank stronger-source value does not erase a valid weaker-source value.
5. Invalid stronger-source value is rejected.
6. Provenance is stored for accepted fields.
7. Parser version is stored where supported.
8. Parse errors are recorded.

---

# 13. Pending Order Draft Tests

## 13.1 Creation

Given a new booking with no matching load:

Expected:

```text
One pending order draft created
Draft linked to conversation key
Draft status set appropriately
```

Running the same draft creation again should not create a duplicate draft.

## 13.2 Reply Merge

Given:

```text
Original delivery date: July 10
Customer reply: Change delivery date to July 14
```

Expected:

```text
Pending draft delivery date = July 14
Communication history retains both messages
```

## 13.3 Dispatcher Override

Given:

```text
Parser value: Warehouse A
Dispatcher-confirmed value: Warehouse B
Later parser run: Warehouse A
```

Expected:

```text
Final warehouse = Warehouse B
Source = dispatcher_confirmed
```

## 13.4 Missing Quantity

Given no valid quantity:

Expected:

```text
container_qty = NULL
draft_status = Needs Details or Needs Dispatcher Review
Create Container Work Orders disabled
```

## 13.5 Polluted Fields

Given a contaminated critical field:

Expected:

```text
Invalid value rejected
Parse error stored
Draft not marked Ready for Order Creation
```

---

# 14. Multi-Container Creation Tests

## 14.1 First Creation

Given:

```text
Booking = RICGX1235800
Container Qty = 4
Existing children = none
```

Expected:

```text
Created sequences = [1, 2, 3, 4]
Existing sequences = []
Failed sequences = []
Total children = 4
```

Each child should contain:

```text
parent_booking_key = RICGX1235800
container_total = 4
is_placeholder_container = true
container_number = NULL
```

## 14.2 Repeated Creation

Run the same creation again.

Expected:

```text
Created sequences = []
Existing sequences = [1, 2, 3, 4]
Total children = 4
```

No duplicates.

## 14.3 Partial Existing Children

Given existing sequences:

```text
1 and 2
```

Expected:

```text
Create only sequences 3 and 4
```

## 14.4 Quantity Increase

Given:

```text
Original quantity = 4
Updated quantity = 6
Existing sequences = 1, 2, 3, 4
```

Expected:

```text
Offer or create only sequences 5 and 6 after confirmation
```

## 14.5 Quantity Decrease

Given:

```text
Original quantity = 4
Updated quantity = 3
```

Expected:

```text
Do not delete sequence 4 automatically
Require dispatcher review
Preserve status and history
```

## 14.6 Blank Container Numbers

Expected:

```text
Placeholder loads may have NULL container_number
```

The booking number must not be copied into the physical container-number field.

## 14.7 Transaction Failure

Simulate one child insert failing.

Expected:

* Transaction rolls back when supported, or
* Successful and failed sequences are clearly recorded
* Retry creates only missing sequences
* No duplicate sequences remain

---

# 15. Physical Container Assignment Tests

Given four placeholder child loads:

```text
Sequence 1 = unassigned
Sequence 2 = unassigned
Sequence 3 = unassigned
Sequence 4 = unassigned
```

Assign:

```text
Sequence 1 = TGHU7654321
```

Expected:

```text
Existing child sequence 1 updated
is_placeholder_container = false
No fifth child created
```

Reject assigning the same physical container number to two active loads unless explicitly allowed by business rules.

---

# 16. Conversation-Key Tests

Verify one canonical conversation-key function.

Test matching by:

1. Booking number
2. Physical container number
3. Customer reference
4. Provider thread ID
5. Normalized subject
6. Fallback intake identifier

Expected:

* All messages for RICGX1235800 share one business conversation.
* A reply with `Re:` remains in the same conversation.
* A forwarded unrelated booking does not merge incorrectly.
* Similar subjects with different booking numbers remain separate.
* Booking number takes precedence over broad subject similarity.

---

# 17. Communication History Tests

Given:

1. Original customer booking
2. Dispatcher reply
3. Customer correction
4. Dispatcher confirmation

Expected:

```text
History count = 4
Chronological order
Inbound and outbound visible
No unrelated messages
```

Each history record should expose:

* Timestamp
* Direction
* Sender
* Subject
* Status
* Body preview
* Full body
* Attachment information

Clicking or selecting a row should show the correct full message.

---

# 18. Active Work-Item Tests

## Waiting Customer

After a dispatcher sends a reply and selects Waiting Customer:

Expected:

```text
Outbound communication saved
Conversation status = Waiting Customer
Current active work item closed
Item removed from active queue
History preserved
```

## Customer Reply

When the customer replies:

Expected:

```text
Same conversation reopened
Newest inbound message becomes actionable
Pending draft preserved
Matched load preserved
Status = Customer Replied or equivalent
```

Do not show every message in the conversation as a separate active work item.

---

# 19. Existing-Load Matching Tests

Test matching by:

* Booking number
* Container number
* Reference number
* Conversation key
* Customer plus supporting fields

Expected result should include:

```text
candidate_load_id
match_reason
match_confidence
booking_number
container_number
customer
status
```

Required cases:

1. Exact booking match.
2. Exact container match.
3. Exact reference match.
4. Multiple possible matches require dispatcher confirmation.
5. No match returns a safe empty result.
6. Load matching does not create a new load automatically.

---

# 20. Email Sync Tests

## 20.1 Duplicate Detection

Given a source Message-ID already stored:

Expected:

```text
Message skipped
No duplicate database row
Skipped count increases
```

## 20.2 Time Budget

Given more messages than can be processed within the configured budget:

Expected:

```text
Sync stops cleanly
stopped_early = true
elapsed time reported
processed messages committed safely
```

## 20.3 Message Limit

Given a quick-sync limit of 8:

Expected:

```text
No more than the configured number of messages processed
```

## 20.4 Conversation Update Batching

Expected:

```text
Touched conversation keys collected
Conversation statuses updated after message loop
No repeated status update for each message
```

## 20.5 Attachment Behavior

With attachment sync disabled:

Expected:

```text
Message metadata imported
Attachment metadata preserved when possible
No deep attachment parsing
```

With attachment sync enabled:

Expected:

```text
Attachments downloaded or processed according to configuration
Errors reported
```

## 20.6 Sync Diagnostics

Expected output includes:

```text
accounts_attempted
messages_fetched
inbound_fetched
outbound_fetched
imported
skipped
errors
triaged
threads_updated
attachments_saved
stopped_early
elapsed_seconds
```

## 20.7 Error Handling

Simulate:

* IMAP connection failure
* Mailbox selection failure
* Message parsing failure
* Database insert failure

Expected:

```text
Error reported
No false success
No unfinished item marked complete
Sync continues safely when appropriate
```

---

# 21. Email Send Tests

Do not send real email during automated tests.

Mock SMTP behavior.

## Successful Send

Expected:

```text
SMTP send called once
Outbound communication saved once
Conversation linked
Work state updated
Success result shown
```

## Failed Send

Expected:

```text
Outbound communication not marked successfully sent
Work item remains active
Error returned
No false completion
```

## Duplicate Send Protection

Repeated callback or rerun should not create duplicate outbound records for the same sent message.

## Reply Headers

Verify correct handling of:

* To
* CC
* Subject
* In-Reply-To
* References
* Reply-all behavior

---

# 22. AI Assistance Tests

Mock the LLM.

Verify AI may:

* Suggest classification
* Suggest action
* Draft a reply
* Identify missing fields
* Support English
* Support Spanish

Verify AI does not automatically:

* Send email
* Create loads
* Update loads
* Change financial values
* Override dispatcher-confirmed values
* Override validated specialized-parser values

When AI fails:

```text
Core deterministic workflow remains usable
Error is shown safely
No operational record is corrupted
```

---

# 23. Database Integrity Tests

Verify:

1. Source Message-ID uniqueness.
2. Parent booking plus sequence uniqueness.
3. Container quantity remains nullable.
4. Invalid quantity does not become 1.
5. Pending draft conversation linkage.
6. Child-load transaction behavior.
7. Required indexes exist.
8. Migrations can be run repeatedly when designed as idempotent.
9. Repository queries match existing schema.
10. Rollback instructions exist for destructive migrations.

Before creating the child uniqueness index, test for existing duplicates.

Example duplicate check:

```sql
select
    parent_booking_key,
    container_sequence,
    count(*)
from public.loads
where parent_booking_key is not null
  and container_sequence is not null
group by
    parent_booking_key,
    container_sequence
having count(*) > 1;
```

---

# 24. Repository and Service Boundary Tests

Verify:

* Page files do not contain new raw SQL.
* Services do not import Streamlit.
* Repositories do not make business decisions.
* One canonical database client is used.
* Compatibility wrappers remain thin.
* Services can be tested without launching Streamlit.
* Repositories can be mocked in service tests.

---

# 25. Streamlit Manual Tests

Automated unit tests do not fully validate Streamlit behavior.

Manually verify:

## Queue Navigation

* Queue tabs load.
* Counts match visible rows.
* Selecting a row opens the correct work item.
* Switching tabs clears stale selections.
* Selected rows remain visually clear.

## Pending Draft

* Draft displays correct values.
* Quantity does not default to 1.
* Fields can be corrected.
* Save action works.
* Readiness state updates.
* Child creation button is disabled when required fields are missing.

## Communication History

* Correct messages appear.
* Rows are clickable.
* Full body opens.
* History is chronological.
* Unrelated messages do not appear.

## Email Action Center

* Reply/forward controls display.
* From, To, CC, and Subject are visible.
* AI draft button works.
* Standard template works.
* Message remains editable.
* Send button is visible.
* Success and errors appear.

## Rerun Behavior

* Button clicks do not execute twice.
* Session-state values initialize correctly.
* State does not leak to another work item.
* Completed work disappears after rerun.
* Customer reply can reopen the conversation.

---

# 26. Security Tests

Verify:

* `.env` is ignored.
* `.streamlit/secrets.toml` is ignored.
* `CLAUDE.local.md` is ignored.
* API keys are not logged.
* Database URLs are not logged.
* SMTP passwords are not logged.
* Customer PDFs are not committed.
* SQL inputs are parameterized.
* Error messages do not expose secrets.
* Temporary files are cleaned safely.

Run:

```powershell
git status --ignored
git grep -n "OPENAI_API_KEY"
git grep -n "DATABASE_URL"
git grep -n "SMTP_PASSWORD"
```

Review results carefully. Environment variable names may appear; secret values must not.

---

# 27. Local Deployment Tests

Verify:

1. Virtual environment activates.
2. Required packages install.
3. Environment configuration loads.
4. Database connection succeeds.
5. Streamlit starts.
6. Operations Inbox page loads.
7. Email sync controls render.
8. No local-only absolute path is required in application logic.
9. No secrets are displayed.

Command:

```powershell
python -m streamlit run app.py
```

---

# 28. Streamlit Cloud Deployment Tests

Verify:

* `requirements.txt` contains required runtime packages.
* Streamlit secrets use the expected names.
* No Windows-only paths are required.
* Filesystem writes use safe temporary or configured storage.
* Database SSL requirements are supported.
* Import paths work on Linux.
* Case-sensitive filenames are correct.
* Customer documents are not included in the repository.
* The application starts without local `.env`.

---

# 29. Performance Test Matrix

Measure before and after major refactors.

| Operation                       | Target                                |
| ------------------------------- | ------------------------------------- |
| Operations Inbox initial render | No attachment parsing or AI call      |
| Queue load                      | Bounded indexed query                 |
| Conversation history            | Loaded only for selected conversation |
| Load matching                   | Loaded on demand                      |
| AI reply                        | Loaded on demand                      |
| Quick email sync                | Approximately 20–30 seconds maximum   |
| Quick sync volume               | Approximately 8–12 messages           |
| Child-load creation             | One transaction or controlled batch   |
| Duplicate lookup                | Indexed lookup, not full-table scan   |

Record:

```text
Operation
Before elapsed time
After elapsed time
Database queries
Rows scanned
Notes
```

---

# 30. Manual RICGX1235800 Acceptance Test

Perform this test after classification, parser, pending-draft, or multi-container changes.

1. Send or import the RICGX1235800 test email.
2. Include the booking-confirmation PDF.
3. Run quick sync.
4. Open the new work item.
5. Confirm it appears in New Orders.
6. Confirm request type is New Booking.
7. Confirm service flow is Export.
8. Confirm booking number is RICGX1235800.
9. Confirm reference is SO217089a/C25749C.
10. Confirm quantity is 4.
11. Confirm size is 40HC.
12. Confirm physical container number is blank.
13. Confirm warehouse is PBP Packaging.
14. Confirm empty pickup is ConGlobal-La Porte.
15. Confirm full return is Bayport Terminal.
16. Confirm carrier is ONE.
17. Confirm vessel is CONTI CORTESIA 013W.
18. Confirm passive invoice terms did not route the item to Billing.
19. Confirm draft shows Ready for Order Creation only after validation.
20. Confirm Create 4 Container Work Orders is available.
21. Confirm dispatcher confirmation is required.
22. Create the child loads.
23. Confirm exactly four children exist.
24. Confirm sequences are 1–4.
25. Confirm container numbers remain blank.
26. Run the creation action again.
27. Confirm zero duplicates are created.
28. Assign one physical container number.
29. Confirm the existing placeholder is updated.
30. Confirm no fifth child load is created.

---

# 31. Required Test Files

Claude should review existing tests before adding files.

Suggested test files include:

```text
tests/test_operations_classification.py
tests/test_email_normalization.py
tests/test_booking_parser.py
tests/test_field_validation.py
tests/test_pending_order_drafts.py
tests/test_multi_container_booking.py
tests/test_conversation_history.py
tests/test_active_work_items.py
tests/test_load_matching.py
tests/test_email_sync.py
tests/test_email_send.py
tests/test_database_integrity.py
```

Do not create duplicate test modules if equivalent files already exist.

---

# 32. Required Change Validation

For every code-change batch, report:

## Tests run

List exact commands.

## Results

Report:

```text
Passed
Failed
Skipped
Warnings
```

## Manual checks

List completed manual checks.

## Remaining gaps

List tests that could not yet be completed and why.

## Regression risk

State which workflows may still be affected.

---

# 33. Definition of Test Completion

A change is ready for commit when:

* Relevant files compile.
* Relevant automated tests pass.
* No unrelated tests fail.
* Database changes are tested safely.
* Required manual Streamlit checks are completed.
* RICGX1235800 regression behavior remains correct.
* No secrets or customer documents are staged.
* Remaining risks are documented.
````
