# Claude Prompt — Calitrans Production Readiness Sprint

You are working in the Calitrans TMS repository. The full review was completed on branch `feat/operations-inbox-web` at commit `5faaf0f8007ac0f1e3073761d57282ed7e1a73ce`.

Your goal is not to perform another broad review. Your goal is to execute the production-readiness remediation plan safely, one independently reviewable work package at a time, while maintaining a durable record of exactly what changed, what was tested, and what remains.

## Sources of truth

Read these before changing code:

- `docs/FULL_CODE_REVIEW.md`
- `docs/SYSTEM_INVENTORY.md`
- `docs/FUNCTIONAL_TEST_MATRIX.md`
- `docs/PRODUCTION_READINESS_GAPS.md`
- `docs/SAMPLE_DATA_GUIDE.md`
- `docs/BUG_TRACKING_WORKFLOW.md`
- `PRODUCTION_READINESS_TRACKER.md`
- All applicable `AGENTS.md`, repository instructions, and architecture documents

Verify that the files and findings exist in the current working tree. Do not rely only on this prompt.

## Mandatory working rules

1. Inspect `git status`, current branch, HEAD, recent commits, and existing changes before editing.
2. Preserve all user changes and unrelated untracked files.
3. Never reset, discard, overwrite, or clean unrelated work.
4. Do not expose secrets or include credentials in logs, test output, commits, reports, or chat.
5. Never connect tests or sample-data tools to production.
6. Do not push, merge, deploy, rotate credentials, change hosted resources, or open/close issues unless explicitly authorized.
7. Complete only the work package selected below. Do not begin the next package.
8. Keep the change narrow. Do not combine unrelated refactoring with a security or reliability fix.
9. Add a regression test for every corrected defect when practical.
10. Update `PRODUCTION_READINESS_TRACKER.md` before finishing.
11. Do not mark an item complete unless its acceptance criteria and verification evidence are satisfied.
12. If blocked, mark it blocked, record the exact reason and required decision, then stop.

## Selected work package

Set this before starting:

`WORK_PACKAGE = PR-01`

If the value is absent, ambiguous, already complete, or has unmet prerequisites, stop and report the issue rather than choosing another package silently.

## Required execution sequence

### 1. Establish baseline

Report:

- Current branch
- HEAD commit
- Working-tree state
- Selected work package
- Finding IDs covered
- Prerequisites
- Files expected to change
- Tests expected to run
- Risks and rollback approach

### 2. Reproduce or verify the finding

Before implementing, confirm the finding using the safest appropriate method:

- Automated test
- Static trace
- Local reproduction
- Disposable-database integration test

Record the evidence. If the finding is no longer present, do not implement a speculative fix. Update the tracker with the evidence and stop.

### 3. Write a narrow implementation plan

State:

- Root cause
- Intended behavior
- Security and authorization impact
- Database or migration impact
- Backward-compatibility impact
- Exact acceptance criteria
- Regression tests to add

### 4. Implement the work package

Follow current repository architecture. Prefer enforcement at the correct boundary:

- Browser/session security at the server-authentication boundary
- Authorization at application-command and API boundaries
- File safety before persistence and parsing
- Data invariants in PostgreSQL plus application validation
- External side-effect idempotency at the outbox/provider boundary
- CI enforcement in pull-request workflows

Avoid temporary bypasses that become a second application path.

### 5. Validate

Run, as applicable:

- Targeted regression tests
- Related subsystem tests
- Backend suite
- Frontend unit tests
- TypeScript typecheck
- ESLint
- Next.js production build
- Python compilation checks
- Migration tests against an explicitly approved disposable database
- `git diff --check`

Report the exact commands and results, including passed, failed, and skipped counts. Do not describe unrun tests as passing.

### 6. Review the diff

Confirm:

- Only intended files changed
- No secret or sensitive value appears in the diff
- No unrelated behavior changed
- Documentation matches actual behavior
- New configuration has safe defaults
- Failure paths are tested
- Rollback or compatibility concerns are documented

### 7. Update the tracker

In `PRODUCTION_READINESS_TRACKER.md`, update only the selected work package and its summary fields. Record:

- Status
- Date
- Branch
- Starting commit
- Completion commit, or `uncommitted`
- Files changed
- Tests and results
- Acceptance evidence
- Remaining risks
- Follow-up issues
- Reviewer/retest status

Use these statuses only:

- `[ ] Not started`
- `[-] In progress`
- `[?] Blocked`
- `[R] Ready for review`
- `[V] Verified`
- `[x] Complete`

`[x] Complete` is permitted only after review/retest is recorded. Implementation alone should normally end at `[R] Ready for review`.

## Ordered work packages

### PR-00 — Credential incident response (manual/external)

Finding: credential appeared in a local traceback.

Scope:

- Rotate the exposed database credential through the provider console.
- Review available access/audit logs.
- Confirm old credentials no longer work.
- Inspect tracked files and Git history for accidental inclusion without printing the credential.
- Document rotation date and evidence without storing the secret.

This requires explicit user action or authorization. Never rotate credentials automatically.

### PR-01 — Commit and protect database test isolation

Finding: CTMS-001.

Scope:

- Review the existing local `conftest.py` and auth-test changes.
- Confirm ambient `DATABASE_URL`, dotenv, and local secret sources cannot leak into pytest.
- Require an explicitly approved disposable database identity for schema/migration tests.
- Add regression tests for refusal behavior.
- Ensure errors and tracebacks sanitize database URLs.

Acceptance criteria:

- Normal unit tests cannot connect to an ambient application database.
- Migration tests refuse to start without explicit disposable configuration.
- Production-like targets are rejected.
- Targeted and broader safe suites pass.

### PR-02 — Disposable PostgreSQL certification

Findings: database-dependent behavior remains uncertified.

Prerequisite: user supplies an approved disposable PostgreSQL URL and exact identity.

Scope:

- Apply migrations from empty database.
- Validate schema readiness.
- Run database integration and migration tests.
- Run sample-data `seed`, `validate`, second `seed`, `list`, `cleanup`, and `reset` flows.
- Prove idempotency and dataset-scoped cleanup.
- Record sanitized evidence.

Do not modify application logic unless a separately tracked defect is discovered.

### PR-03 — Upload and parser hardening

Finding: CTMS-002.

Scope:

- Enforce request and decoded byte limits before persistence.
- Validate signatures/magic bytes rather than trusting name or MIME alone.
- Define supported file types.
- Reject dangerous or mismatched files.
- Apply archive member, expansion, nesting, page, parser-time, and resource limits where relevant.
- Generate safe user-facing errors and sanitized internal logs.
- Cover API, Streamlit, inbox attachments, and worker/parser paths consistently.

Acceptance criteria:

- Oversized, spoofed, unsupported, malformed, and archive-bomb-like fixtures fail safely.
- Valid supported documents continue to work.
- No partial metadata/file residue remains after rejection.

### PR-04 — Harden browser and Streamlit sessions

Finding: CTMS-003.

Scope:

- Select and document the canonical session model.
- Move browser authentication away from script-readable token storage to secure, HttpOnly, appropriately SameSite cookies.
- Add CSRF protection where cookie authentication creates exposure.
- Define expiration, revocation, logout, refresh, and cross-origin behavior.
- Determine the safe interim Streamlit model without inventing a second long-term auth architecture.

Acceptance criteria:

- Application scripts cannot read bearer/session secrets.
- Logout invalidates the intended session.
- CSRF and CORS tests cover permitted and rejected origins/actions.
- Authorization behavior remains unchanged or intentionally documented.

### PR-05 — Database lifecycle constraints

Finding: CTMS-005.

Scope:

- Inventory persisted status and retry fields.
- Reconcile actual application values before adding constraints.
- Add check constraints for supported lifecycle values and nonnegative counters.
- Add safe migrations with preflight detection of invalid existing rows.
- Preserve application-level validation and useful error messages.

Acceptance criteria:

- Invalid lifecycle values and negative retry counts are rejected by PostgreSQL.
- Existing valid records migrate successfully.
- Migration behavior for invalid legacy data is explicit and tested.

### PR-06 — SMS/outbox idempotency and crash recovery

Finding: CTMS-004.

Scope:

- Trace the exact crash window between provider acceptance and local success persistence.
- Determine provider idempotency/reconciliation capability.
- Add stable provider idempotency keys where supported.
- Add reconciliation for ambiguous delivery results.
- Prevent automatic resend when the prior outcome is unknown unless policy explicitly allows it.
- Add crash-point, retry, duplicate-callback, and concurrent-worker tests.

Acceptance criteria:

- A crash after provider acceptance does not cause an uncontrolled duplicate send.
- Ambiguous outcomes enter an operator-visible reconciliation state.
- Retries remain safe and auditable.

### PR-07 — Mandatory pull-request CI

Finding: CTMS-008.

Scope:

- Add pull-request CI for backend tests, frontend tests, lint, typecheck, production build, Python compilation, and migration validation.
- Add dependency/security scanning appropriate to the repository.
- Separate fast required checks from credential-dependent certification jobs.
- Add concurrency cancellation and useful cache behavior.
- Document required branch-protection checks.

Acceptance criteria:

- A pull request automatically runs the required safe checks.
- Failures block merge after branch protection is configured.
- CI cannot inherit or contact production resources.

### PR-08 — Persistent workers and operational recovery

Finding: CTMS-006.

Scope:

- Decide hosting topology before implementation.
- Replace or supplement scheduled GitHub processors with persistent workers.
- Add health, lag, retry, terminal-failure, and dead-letter visibility.
- Define deploy, shutdown, restart, and recovery behavior.

### PR-09 — Canonical path and Streamlit retirement plan

Finding: CTMS-007.

Scope:

- Select Next.js → FastAPI → application/repository → PostgreSQL as the default target unless evidence requires revision.
- Inventory duplicated Streamlit and web workflows.
- Select migration order by operational value and risk.
- Freeze new feature development in superseded paths.
- Add removal acceptance criteria per page/workflow.

### PR-10 — Documentation and configuration cleanup

Finding: CTMS-009.

Scope:

- Correct README, configuration, setup, testing, worker, sample-data, and deployment documentation.
- Remove or clearly label obsolete instructions.
- Ensure every documented command is executed or mechanically validated.

## Final response

Return:

1. Selected work package and disposition
2. Baseline branch and commit
3. Finding reproduction evidence
4. Root cause
5. Changes made
6. Files changed
7. Tests run and exact results
8. Acceptance-criteria results
9. Tracker updates
10. Remaining risks or blockers
11. Suggested commit message
12. Exact next human action

Stop after the selected work package. Do not start the next package.
