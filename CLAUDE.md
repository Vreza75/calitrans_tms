# CaliTrans TMS — Claude Code Project Instructions

## Mission

Maintain, review, refactor, and improve the existing CaliTrans TMS application.

Do not rewrite the application from scratch.

Preserve working behavior, database compatibility, email workflows, dispatcher workflows, and Streamlit Cloud deployment unless a change is clearly justified.

Reuse existing services, repositories, UI components, parsers, and AI agents whenever practical.

Make changes in small, testable batches.

---

## Active Repository

Only modify this repository:

`C:\GitHub\calitrans_tms_postgres_upgrade_clean`

Do not modify or copy code from the obsolete repository unless specifically instructed:

`C:\Users\vreza\OneDrive\Documents\GitHub\calitrans_tms_postgres_upgrade`

Before editing code, confirm:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git log --oneline -10
#Business Context

CaliTrans is a small drayage and transportation company with approximately:

10–20 drivers
One dispatcher
One manager
One accounting representative

The TMS supports:

Import container moves
Export container moves
Local import
Local export
Warehouse-to-warehouse transportation
New bookings
Booking updates
Terminal and gate changes
Appointment and PIN requests
Driver and delivery status updates
POD and document requests
Billing readiness
Customer email communication

Favor reliability, maintainability, clear workflows, and low operating cost over unnecessary enterprise complexity.

The system should support English and Spanish customer communication.

#Current Technology

Do not force a FastAPI or background-worker migration during routine cleanup.

FastAPI and background workers may be recommended as later phases.

#Architecture Rules

Use these responsibilities:

app.py — application entry point and routing
pages_app/ — page orchestration and Streamlit page logic
ui_components/ — reusable Streamlit interface sections
services/ — business workflows and domain logic
application/ — framework-neutral query/command services shared by Streamlit and FastAPI (no streamlit import; see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md)
api/ — FastAPI routers/schemas calling application/, versioned under /api/v1
repositories/ — database queries and persistence
database/ — database configuration, migrations, and compatibility wrappers
models/ — typed domain models when introduced
tests/ — automated regression tests

Rules:

Do not add raw SQL to Streamlit page files.
Do not add Streamlit calls to service or repository modules.
Do not create duplicate helpers without first finding the existing implementation.
Do not create compatibility aliases without documenting why they are required.
Use one canonical classification pipeline.
Use one canonical business-conversation-key function.
Use one canonical pending-order-draft merge function.
Use one canonical work-item completion workflow.
Use one canonical multi-container child-load creation workflow.

Do not split files only to reduce line count. Split by responsibility and data flow.

Operations Inbox-specific rules (purpose/workflow, classification precedence, multi-container booking, parsing rules, pending draft rules, conversation/work-item rules, email action center, email sync rules, required reading) live in `.claude/rules/operations-inbox.md` and load automatically when working in that area.

#AI Rules

AI may:

Suggest classification
Suggest next action
Draft customer replies
Support English and Spanish
Learn from dispatcher corrections

AI may not:

Send email without confirmation
Create loads without confirmation
Change operational records without confirmation
Change financial records without confirmation
Replace deterministic parsing for stable customer formats
#Code Review and Dead-Code Rules

Do not remove code merely because it appears unused.

Before deleting code:

Search imports.
Search direct call sites.
Search aliases.
Search dynamic access such as getattr.
Search Streamlit callback keys.
Check routers and deployment entry points.
Check tests.
Check configuration-driven references.

Classify candidates as:

Confirmed dead
Duplicate
Deprecated but referenced
Compatibility shim
Debug-only
Production-required
Uncertain

Delete only confirmed dead or safely consolidated duplicate code.

Pay special attention to:

pages_app/operations_inbox.py
services/operations_inbox_service.py
Email-sync services
Parser services
Attachment services
Pending draft code
Conversation-history code
Load-matching code
Root db_client.py
database/db_client.py
Compatibility aliases
Old backup modules
Debug panels
Unused migrations
#Database Safety

Never run destructive database changes without first providing:

Purpose
Migration SQL
Database impact
Existing-data impact
Rollback SQL
Files affected
Test plan

Database migrations should be:

Versioned
Idempotent when practical
Documented
Committed to the repository

Do not place migration fragments inside page files.

Do not automatically treat ocean rate-sheet charges as CaliTrans customer trucking rates.

#Secrets and Files

Never commit:

.env
.streamlit/secrets.toml
CLAUDE.local.md
.venv/
API keys
Passwords
Database URLs
Customer email attachments
Customer PDF documents
storage/
backups/
__pycache__/
*.pyc
#Required Development Workflow

Before broad changes:

Confirm the repository.
Inspect the current implementation.
Run compile and available tests.
Report findings.
Present a phased plan.
Ask for approval before risky or schema-changing work.
Implement small batches.
Test after each batch.
Commit one concern per commit.

Do not begin with a large rewrite.

Standard Commands
python -m compileall app.py pages_app services ui_components repositories database utils ai_agents ai_core
pytest -q
python -m streamlit run app.py

#First Audit Response

Before broad code changes, provide:

Current repository root and branch
Git status
Compile result
Test result
Top 10 critical findings
Confirmed duplicate and dead-code candidates
Current Operations Inbox data flow
Database and migration risks
Proposed refactor sequence
Changes requiring approval

Wait for approval before broad refactoring or destructive database work.