
# CaliTrans TMS Code Review and Refactor Playbook

## 1. Purpose

This document defines how Claude Code should review, clean, refactor, test, and improve the CaliTrans TMS repository.

The review must improve the existing application without rewriting it from scratch.

Primary goals:

- Preserve working behavior
- Remove confirmed dead code
- Consolidate duplicate logic
- Improve reliability
- Improve email-processing performance
- Improve parser accuracy
- Reduce large, hard-to-maintain functions
- Separate UI, business logic, and persistence
- Improve database consistency
- Add regression tests
- Protect Streamlit Cloud deployment
- Protect existing dispatcher workflows

The review must proceed in small, testable phases.

---

## 2. Active Repository

Only review and modify:

```text
C:\GitHub\calitrans_tms_postgres_upgrade_clean
````

Do not modify or copy code from:

```text
C:\Users\vreza\OneDrive\Documents\GitHub\calitrans_tms_postgres_upgrade
```

Before reviewing code, confirm:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git log --oneline -10
python --version
```

Stop if the repository root is not the clean repository.

---

## 3. Review Principles

The review must follow these principles:

1. Do not rewrite the application from scratch.
2. Do not remove code solely because it appears unused.
3. Do not change behavior without documenting the reason.
4. Do not make destructive database changes without approval.
5. Do not mix unrelated refactors into one change.
6. Do not commit secrets or customer documents.
7. Prefer focused improvements over broad rewrites.
8. Preserve compatibility unless a replacement is tested.
9. Add tests before or alongside risky changes.
10. Measure performance before claiming improvement.

---

## 4. Required Initial Audit

Before changing code, perform a read-only audit.

Collect:

* Repository root
* Current branch
* Git status
* Recent commits
* Python version
* Dependency files
* Current project tree
* Compile results
* Test results
* Largest modules
* Largest functions
* Entry points
* Import relationships
* Database access paths
* Streamlit page structure
* Operations Inbox data flow
* Email sync data flow
* Parser data flow
* Pending-draft data flow
* Multi-container creation flow
* Deployment configuration

Run:

```powershell
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core
pytest -q
```

Do not modify code during this phase.

---

## 5. Required Initial Report

Before broad modifications, provide:

1. Current repository root
2. Current branch
3. Git status
4. Compile result
5. Test result
6. Top 10 critical findings
7. High-risk modules
8. Duplicate-code candidates
9. Dead-code candidates
10. Database and migration risks
11. Performance risks
12. Missing tests
13. Proposed refactor phases
14. Changes requiring approval

Wait for approval before broad refactoring or schema changes.

---

## 6. Repository Areas to Review

Review the complete repository, including:

```text
app.py
pages_app/
services/
ui_components/
repositories/
database/
utils/
ai_agents/
ai_core/
tests/
.streamlit/
requirements.txt
pyproject.toml
.gitignore
```

Pay special attention to:

```text
pages_app/operations_inbox.py
services/operations_inbox_service.py
```

Also review:

* Email sync services
* Email parser services
* Attachment parser services
* Pending draft logic
* Conversation history
* Load matching
* Case management
* AI feedback storage
* SMTP and IMAP configuration
* Root database client
* `database/db_client.py`
* Compatibility wrappers
* Old application files
* Backup modules
* Debug modules
* SQL migrations

---

## 7. Dead-Code Review Rules

Do not delete code merely because it looks unused.

Before marking code dead:

1. Search direct imports.
2. Search direct function calls.
3. Search aliases.
4. Search compatibility wrappers.
5. Search `getattr` and dynamic access.
6. Search Streamlit callback keys.
7. Search router and page entry points.
8. Search configuration-driven references.
9. Search tests.
10. Search deployment scripts.
11. Search SQL function references.
12. Search external integration references.

Use tools such as:

```powershell
rg "function_name" .
rg "ClassName" .
ruff check .
pytest -q
```

Optional tools may include:

```text
vulture
pyright
coverage
import-linter
```

Only add tools that are safe for the repository.

Classify every candidate as:

```text
Confirmed dead
Duplicate
Deprecated but referenced
Compatibility shim
Debug-only
Production-required
Uncertain
```

Only confirmed dead code should be deleted automatically.

Uncertain code should remain and be documented.

---

## 8. Duplicate-Code Review

Search for duplicate implementations of:

* Classification
* Queue routing
* Conversation-key generation
* Active work-item filtering
* Communication-history loading
* Pending-draft merging
* New-booking detection
* Billing detection
* Attachment parsing
* Container quantity parsing
* Date parsing
* Load matching
* Email completion
* SMTP reply saving
* AI feedback saving
* Child-load creation
* Database connection logic

For each duplicate:

1. Identify all versions.
2. Identify all call sites.
3. Select the canonical implementation.
4. Add tests around current behavior.
5. Move callers to the canonical implementation.
6. Remove the obsolete version.
7. Document compatibility aliases that remain.

Do not leave two implementations with slightly different rules.

---

## 9. Large Module Review

Identify modules and functions that are too large or combine unrelated responsibilities.

Review:

* File line count
* Function line count
* Number of branches
* Number of database calls
* Number of external dependencies
* Number of session-state values
* Number of responsibilities

Large code is not automatically wrong.

Refactor only when responsibilities can be separated clearly.

Examples of valid separation:

```text
UI rendering
Business logic
Database persistence
Email normalization
Classification
Parsing
Conversation tracking
Pending drafts
Child-load creation
AI assistance
```

Do not split code only to reduce line count.

---

## 10. Architecture Boundaries

Target responsibilities:

### `app.py`

* Application entry point
* Page routing
* Global configuration
* Minimal orchestration

### `pages_app/`

* Streamlit page layout
* User-action orchestration
* Calling services
* Displaying results

### `ui_components/`

* Reusable Streamlit sections
* Forms
* Tables
* Panels
* Dialogs

### `services/`

* Business workflows
* Classification
* Conversation logic
* Draft merging
* Child-load creation
* Email synchronization
* Attachment parsing orchestration

### `repositories/`

* SQL
* Database reads
* Database writes
* Transactions
* Persistence models

### `models/`

* Typed request objects
* Typed result objects
* Domain enums
* Validation models

### `tests/`

* Unit tests
* Integration tests
* Regression fixtures
* Database behavior tests

Rules:

* Do not place raw SQL in Streamlit page files.
* Do not call Streamlit from service or repository modules.
* Do not place business decisions inside repository functions.
* Do not duplicate database access logic across services.
* Do not use page-local helpers as application-wide business logic.

---

## 11. Operations Inbox Review

Review the complete Operations Inbox flow:

```text
Email sync
→ message storage
→ body normalization
→ attachment storage
→ parsing
→ classification
→ conversation key
→ load matching
→ pending draft
→ dispatcher decision
→ reply or action
→ completion
→ history
```

Verify:

* One canonical classification result
* One canonical conversation key
* One canonical queue assignment
* One canonical pending-draft merge
* One canonical work-item completion function
* One canonical multi-container creation service

Identify any page function that independently recalculates these values.

---

## 12. Classification Review

Review all classification paths:

* Initial email insertion
* Fast triage
* AI classification
* Batch recheck
* Queue table
* Dispatcher Decision
* Pending draft
* Manual correction

Verify the same precedence is applied everywhere:

1. Spam / no-action
2. Quote
3. Booking confirmation
4. Appointment / PIN
5. Actual billing request
6. Document / POD
7. Existing-load update
8. Needs review

A booking confirmation must not become Billing because it contains:

* Bill of lading
* Rate sheet
* Invoice contact
* Charges will be invoiced
* This document is not an invoice

Actual billing must require an actionable request.

Add regression tests before consolidating classification logic.

---

## 13. Parser Review

Review parser responsibilities separately:

### Email-body normalization

Verify:

* Plain text is preserved
* HTML is preserved
* HTML tables are normalized
* Signatures are separated
* Quoted replies are handled
* Latest customer message is extracted

### Deterministic email parser

Verify stable labels are parsed correctly.

### Specialized document parser

Verify known customer documents use profile-specific parsing.

### Generic parser

Verify it fills only blank fields.

### LLM parser

Verify it is used only when deterministic parsing is uncertain.

### Field validation

Reject polluted values containing embedded labels.

### Field provenance

Store:

* Value
* Source
* Parser
* Version
* Confidence
* Review status

---

## 14. Multi-Container Review

Use:

```text
docs/MULTI_CONTAINER_BOOKING_SPEC.md
```

as the authoritative requirement.

Verify:

* One parent booking
* Multiple child loads
* Quantity remains nullable when unknown
* No silent default to 1
* Booking number is not saved as container number
* Child sequences are unique
* Child creation is idempotent
* Existing child count comes from the database
* Partial failure is recoverable
* Creation is dispatcher-confirmed
* Physical container numbers may remain blank

Use booking `RICGX1235800` as the regression fixture.

---

## 15. Conversation and Threading Review

Verify email threading uses:

* Message-ID
* In-Reply-To
* References
* Provider thread ID
* Booking number
* Container number
* Reference number
* Normalized subject

Verify:

* Inbound and outbound messages stay in one conversation.
* Completed messages leave active work.
* Customer replies reopen the same conversation.
* History excludes unrelated messages.
* History is chronological.
* Full messages can be opened.
* Outbound messages are not duplicated.

---

## 16. Pending Draft Review

Verify one pending draft exists per unresolved booking conversation.

Review:

* Draft creation
* Draft update
* Follow-up reply merging
* Dispatcher edits
* Source precedence
* Field provenance
* Missing-field calculation
* Draft readiness
* Cancellation
* Load creation
* Child-load creation
* Final draft status

A weaker parser must not overwrite a dispatcher-confirmed value.

A draft must not be marked ready when fields are polluted or missing.

---

## 17. Email Sync Review

Review:

* IMAP connection
* Mailbox selection
* Message limits
* Time budgets
* Duplicate detection
* Existing-message lookup
* Attachment handling
* Sent-mail sync
* Conversation update batching
* Error reporting
* Streamlit reruns
* Sync-lock behavior

Interactive target:

```text
8–12 messages
20–30 second time budget
Inbox only by default
Sent optional
Attachments optional
```

Check for:

* Full-table scans
* N+1 queries
* Attachment parsing inside quick sync
* Repeated conversation updates
* Undefined loop variables
* Multiple simultaneous syncs
* Silent exceptions
* Excessive message lookup sizes

Quick sync should return diagnostics.

---

## 18. Email Action Review

Verify the Email Action Center:

* Displays Reply, Reply All, and Forward
* Shows From, To, CC, and Subject
* Includes AI draft assistance
* Includes standard templates
* Keeps send button visible
* Saves outbound communication
* Saves failure states
* Prevents duplicate outbound records
* Updates conversation status
* Removes completed work
* Returns to the active queue

The system must not mark an item complete when send fails.

---

## 19. AI Review

Review AI use for:

* Classification
* Reply drafting
* Missing-field suggestions
* Load-match suggestions
* Dispatcher feedback
* English and Spanish support

Verify AI does not automatically:

* Send email
* Create loads
* Update loads
* Change financial values
* Override deterministic parser output
* Override dispatcher-confirmed values

Remove or hide obsolete AI debug panels from production UI.

---

## 20. Database Review

Review schema and usage for:

```text
order_intake
order_intake_drafts
loads
operations cases
communications
attachments
AI feedback
settings
```

Compare:

* Python SQL
* Repository queries
* Migration files
* Supabase schema
* Index definitions
* Unique constraints
* Null/default behavior

Identify:

* Columns used in code but missing from migrations
* Columns added but unused
* Conflicting column names
* Incorrect default values
* Missing indexes
* Duplicate records
* Missing constraints
* Direct SQL in page code
* Transaction gaps

Do not make destructive changes without approval.

---

## 21. Database Client Review

Review:

```text
db_client.py
database/db_client.py
database/__init__.py
```

Determine:

* Which file is canonical
* Whether one is a compatibility wrapper
* Whether imports are consistent
* Whether duplicate connection pools exist
* Whether secrets are loaded consistently
* Whether Streamlit and local execution behave the same

Do not keep two independent database implementations.

A compatibility wrapper may remain if it is thin and documented.

---

## 22. Migration Rules

All schema changes should be represented by migration files.

A migration proposal must include:

* Purpose
* Forward SQL
* Rollback SQL
* Existing-data impact
* Code dependencies
* Index impact
* Deployment order
* Test plan

Migrations should be idempotent when practical.

Do not place SQL fragments only in chat instructions or page code.

---

## 23. Performance Review

Measure before and after.

Review:

* Operations Inbox initial page load
* Queue query
* Selected-message load
* History load
* Pending-draft load
* Load matching
* Email sync
* Attachment parsing
* AI request time
* SQL query count
* Rows scanned
* Streamlit reruns

Create a table:

```text
Operation
Before
After
Queries
Rows scanned
Elapsed time
Notes
```

Do not claim improvement without measurements.

---

## 24. Streamlit Review

Review:

* Session-state initialization
* Session-state cleanup
* Unique widget keys
* Rerun behavior
* Button nesting
* Form submission
* Expensive work during render
* Disabled-state logic
* Selected-row handling
* Error-display behavior
* Cloud compatibility

Look for:

* Widgets inside conditional branches that disappear unexpectedly
* Buttons nested inside unrelated submit handlers
* Variables used before assignment
* Session-state values never cleared
* Repeated expensive queries on every rerun

---

## 25. Exception and Logging Review

Search for:

```python
except Exception:
    pass
```

Classify each occurrence.

Silent exception handling is not acceptable for:

* Email sync
* Email send
* Load creation
* Draft updates
* Database writes
* Multi-container creation
* Parser failures
* Conversation updates

Replace with:

* Structured logging
* Safe user-facing errors
* Preserved unfinished state
* Error details without secrets
* Useful diagnostics

Silent handling may remain only when failure is truly optional and documented.

---

## 26. Security Review

Verify:

* Secrets are not committed
* Secrets are not printed
* Database URLs are not logged
* SMTP credentials are not exposed
* OpenAI keys are not exposed
* Customer documents are not committed
* Local storage is ignored
* SQL uses parameters
* Uploaded files are validated
* User input is not interpolated into SQL
* Error messages do not expose secrets

Check `.gitignore` for:

```text
.env
.streamlit/secrets.toml
CLAUDE.local.md
.venv/
storage/
backups/
__pycache__/
*.pyc
```

---

## 27. Dependency Review

Review:

* `requirements.txt`
* `pyproject.toml`
* Runtime Python version
* Duplicate packages
* Unpinned dependencies
* Unused dependencies
* Streamlit compatibility
* PostgreSQL driver compatibility
* PDF parser compatibility
* OpenAI SDK compatibility

Do not upgrade all dependencies at once.

Separate dependency upgrades from code refactors.

---

## 28. Testing Strategy

Add tests before risky refactors.

Required areas:

### Classification

* Booking confirmation versus Billing
* Quote routing
* Appointment routing
* Existing-load update routing
* Archive routing

### Parsing

* HTML table parsing
* PDF booking parsing
* Field contamination rejection
* Source precedence
* Container quantity and size normalization

### Conversation

* Inbound and outbound threading
* Reply reopening
* History filtering
* Latest active work item

### Pending drafts

* Initial creation
* Follow-up merging
* Dispatcher override preservation
* Missing fields
* Readiness state

### Multi-container creation

* Exact child count
* Unique sequences
* Idempotency
* Partial retry
* Blank container numbers

### Email sync

* Deduplication
* Time budget
* Batched thread updates
* Error reporting
* Attachment behavior

### Email send

* Successful send
* Failed send
* Outbound history
* Completion state

---

## 29. Standard Verification Commands

Run after each batch:

```powershell
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core
pytest -q
```

Run the application:

```powershell
python -m streamlit run app.py
```

Also run relevant focused tests:

```powershell
pytest tests/test_operations_classification.py -q
pytest tests/test_booking_parser.py -q
pytest tests/test_multi_container_booking.py -q
```

Use actual file names present in the repository.

---

## 30. Refactor Sequence

Recommended sequence:

### Phase 1 — Audit

* No behavior changes
* Produce findings
* Map data flow
* Identify risks

### Phase 2 — Safe cleanup

* Remove unused imports
* Remove confirmed dead code
* Remove verified duplicates
* Improve logging
* Clean `.gitignore`
* Document wrappers

### Phase 3 — Canonical models

Introduce typed models for:

* Classification
* Parsed booking
* Conversation identity
* Pending draft
* Multi-container result
* Email sync result

### Phase 4 — Classification

Create one canonical classification service.

### Phase 5 — Parsing

Separate:

* Email normalization
* Structured email parser
* Specialized document profiles
* Generic parser
* LLM fallback
* Field validation
* Provenance

### Phase 6 — Conversation service

Consolidate:

* Conversation key
* Thread relationships
* History
* Status transitions
* Active work behavior

### Phase 7 — Pending draft service

Consolidate:

* Draft create
* Draft merge
* Dispatcher corrections
* Readiness
* Load conversion

### Phase 8 — Multi-container service

Consolidate:

* Existing-child query
* Missing-sequence calculation
* Transactional creation
* Idempotency
* Result reporting

### Phase 9 — Email action service

Consolidate:

* Reply
* Forward
* Save outbound
* Completion
* Waiting-customer status

### Phase 10 — Email sync service

Consolidate:

* Fetch
* Deduplicate
* Insert
* Batch thread update
* Diagnostics
* Time budget

### Phase 11 — UI extraction

Move isolated sections into reusable UI components.

### Phase 12 — Performance

Measure and optimize.

### Phase 13 — Regression hardening

Add tests, documentation, and deployment checks.

---

## 31. Target Operations Inbox Structure

Review existing modules before adding new files.

Desired direction:

```text
pages_app/
    operations_inbox.py

ui_components/
    operations_inbox/
        sync_controls.py
        queue_table.py
        dispatcher_decision.py
        conversation_history.py
        pending_order_draft.py
        email_action_center.py
        attachment_review.py
        learning_feedback.py

services/
    operations_email_sync_service.py
    operations_classification_service.py
    operations_conversation_service.py
    operations_pending_draft_service.py
    operations_multi_container_service.py
    operations_attachment_service.py
    operations_ai_reply_service.py

repositories/
    order_intake_repo.py
    order_draft_repo.py
    load_repo.py
    conversation_repo.py
    operations_case_repo.py
    ai_feedback_repo.py

models/
    classification_models.py
    booking_models.py
    operations_intake_models.py
```

Do not create duplicate modules if equivalent files already exist.

---

## 32. Git Workflow

Before major refactoring:

```powershell
git status
git checkout -b audit/operations-inbox-review
```

Use small commits.

Examples:

```text
Add Operations Inbox regression tests
Remove duplicate queue-routing helper
Move conversation SQL into repository
Add booking classification guard
Add multi-container creation service
Improve quick-sync diagnostics
```

Rules:

* One concern per commit
* No force push
* No secrets
* No customer PDFs
* No unrelated formatting changes
* No schema changes hidden inside UI commits

---

## 33. Required Documentation Deliverables

Claude should create or update:

```text
docs/CODE_AUDIT.md
docs/DEAD_CODE_REPORT.md
docs/OPERATIONS_INBOX_DATA_FLOW.md
docs/OPERATIONS_INBOX_REFACTOR_PLAN.md
docs/DATABASE_MIGRATION_REVIEW.md
docs/PERFORMANCE_REVIEW.md
docs/TEST_MATRIX.md
docs/DEPLOYMENT_CHECKLIST.md
```

---

## 34. Required Dead-Code Report Format

Use:

| File / Function | Classification | Evidence | Call Sites Checked | Risk | Recommendation |
| --------------- | -------------- | -------- | ------------------ | ---- | -------------- |

Examples:

```text
Confirmed dead
Duplicate
Compatibility shim
Debug-only
Production-required
Uncertain
```

---

## 35. Required Change Report

For every completed batch, report:

### Summary

What changed.

### Reason

Why the change was needed.

### Files changed

List exact files.

### Database impact

State whether schema or data changed.

### Tests run

List exact commands.

### Results

State pass or fail.

### Remaining risk

Document unresolved concerns.

### Next step

Recommend the next focused batch.

---

## 36. Approval Requirements

Ask for approval before:

* Dropping a database column
* Renaming a production column
* Deleting production data
* Changing load-status semantics
* Changing billing behavior
* Replacing the database client
* Changing authentication
* Migrating to FastAPI
* Adding a background worker
* Removing a compatibility wrapper
* Changing deployment infrastructure
* Performing a broad UI rewrite

Safe cleanup may proceed after the audit plan is approved.

---

## 37. Definition of Done

The review is complete when:

* Code compiles.
* Tests pass.
* Confirmed dead code is removed.
* Duplicate logic is consolidated.
* Operations Inbox responsibilities are separated.
* Queue routing is consistent.
* Booking confirmations do not route to Billing incorrectly.
* RICGX1235800 parses as four 40HC containers.
* Multi-container creation is idempotent.
* Replies remain in one conversation.
* Completed work leaves active queues.
* Pending drafts retain latest agreed values.
* Email sync is bounded and responsive.
* Database migrations match code.
* Direct SQL is removed from page files where practical.
* Streamlit calls are removed from services.
* Errors are logged and visible.
* Secrets and customer documents remain excluded.
* Local and Streamlit Cloud deployment are documented.
* A prioritized backlog remains for deferred work.

````

