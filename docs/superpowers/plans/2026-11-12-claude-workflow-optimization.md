# Claude Code Workflow Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Claude Code's fixed per-turn context cost and repeated-discovery cost in this repo without changing any application behavior — config/docs/rules/skill only.

**Architecture:** Split root `CLAUDE.md` so universal mandatory rules (security, authorization, the core database-destructive-change restriction, secrets, PR workflow) stay always-loaded, while path-bound detail (DB migration mechanics, Operations-Inbox-specific dead-code warnings, frontend generated-file warning) moves into `.claude/rules/*.md` files that only load when the matching path is touched. Add a `tms-feature` skill that points at canonical locations instead of re-deriving them each session, and a `plans/INDEX.md` so prior-art lookup doesn't require reading all 13 plan files. Remove a confirmed-dead PostToolUse hook. Register (not retrofit) pytest markers for future targeted-test use.

**Tech Stack:** Markdown (CLAUDE.md, rules, skill), `pytest.ini` (INI), no application code.

## Global Constraints

- Zero application code changes — `app.py`, `pages_app/`, `services/`, `ui_components/`, `repositories/`, `database/`, `api/`, `application/`, `workers/`, `web/app` source untouched.
- Do not bulk-modify existing test files (no marker retrofit).
- Do not create a second planning directory — all plan/spec output stays under `docs/superpowers/plans/` and `docs/superpowers/specs/`.
- Universal database-safety restriction (the destructive-change disclosure list) must remain visible, unmodified, in root `CLAUDE.md`.
- Branch `chore/claude-workflow-optimization` only — never touches `feat/inbox-backfill-learning-phase` / PR #19.
- No new agents.

---

### Task 1: Register pytest markers (additive, no test files touched)

**Files:**
- Create: `pytest.ini`

**Interfaces:**
- Produces: marker names `inbox`, `attachments`, `backfill`, `worker`, `api` — available for future tests to opt into; no existing test is modified to use them.

- [ ] **Step 1: Write the file**

```ini
[pytest]
markers =
    inbox: Operations Inbox classification, parsing, and conversation tests
    attachments: durable attachment storage, retrieval, and retention tests
    backfill: historical backfill and live/historical classification tests
    worker: worker job queue and job-handler tests
    api: FastAPI router and application-layer tests
```

**Correction made during execution:** an earlier draft of this task included
`addopts = -q`. Discovered during Task 9 full-suite verification: CLI `-q`
(as used in CLAUDE.md's `#Standard Commands` and the `tms-feature` skill)
plus `addopts = -q` stacks into pytest's double-quiet level, which
suppresses the final `N passed, M skipped` summary line entirely. Removed
`addopts` — the markers-only file doesn't have this problem.

- [ ] **Step 2: Verify pytest accepts the config and nothing collects differently**

Run: `python -m pytest --markers | head -20`
Expected: the five custom markers listed alongside pytest's built-ins, no error.

Run: `python -m pytest -q --collect-only 2>&1 | tail -5`
Expected: same collected-test count as before this change (no filtering applied yet, `addopts = -q` only changes verbosity default).

- [ ] **Step 3: Commit**

```bash
git add pytest.ini
git commit -m "chore: register pytest markers for future targeted test runs"
```

---

### Task 2: Extract path-scoped database/worker/script safety rule

**Files:**
- Create: `.claude/rules/database-safety.md`
- Modify: `CLAUDE.md:160-181` (the `#Database Safety` section)

**Interfaces:**
- Consumes: nothing.
- Produces: a `paths:`-scoped rule file following the exact frontmatter pattern already used by `.claude/rules/operations-inbox.md`, so the harness's existing path-rule loader picks it up without any code change.

- [ ] **Step 1: Write the new rule file**

```markdown
---
paths:
  - "database/**"
  - "repositories/**"
  - "workers/**"
  - "scripts/**"
  - "**/*migration*"
  - "tests/integration/**"
---

# Database, Worker, and Script Safety

The root `CLAUDE.md` `#Database Safety` section states the universal
restriction — never run a destructive change without disclosing purpose,
migration SQL, database impact, existing-data impact, rollback SQL, files
affected, test plan — and two cross-cutting warnings (no migration
fragments in page files; don't treat ocean rate-sheet charges as trucking
rates). That restriction always applies here; this file adds path-specific
detail, it does not replace it.

## Migrations

Migrations in `database/` must be:

Versioned
Idempotent when practical
Documented
Committed to the repository

## Workers and scripts

`workers/` job handlers and `scripts/` (e.g. `scripts/backfill_inbox.py`,
`scripts/scrub_attachment_base64.py`) that mutate persisted data follow the
same disclosure requirement as a migration — a script that scrubs or
backfills production rows is a destructive change even without a `.sql`
file.

## Integration tests

Tests under `tests/integration/` that exercise real migrations or
repository writes must run against synthetic data and a disposable/test
database only — never a production dump or production connection string.
```

- [ ] **Step 2: Edit `CLAUDE.md` lines 160-181**

Replace:
```
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
```

With:
```
#Database Safety

Never run destructive database changes without first providing:

Purpose
Migration SQL
Database impact
Existing-data impact
Rollback SQL
Files affected
Test plan

Do not place migration fragments inside page files.

Do not automatically treat ocean rate-sheet charges as CaliTrans customer trucking rates.

Migration versioning/idempotency requirements and worker/script/integration-test specifics live in `.claude/rules/database-safety.md` and load automatically when working in those paths.
```

- [ ] **Step 3: Verify the universal restriction is still intact and unmodified in root**

Run: `grep -n "Never run destructive database changes" CLAUDE.md`
Expected: one match, still present.

Run: `grep -c "" .claude/rules/database-safety.md`
Expected: non-zero, file exists and is non-empty.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/database-safety.md
git commit -m "chore: path-scope DB migration/worker/script safety detail, keep universal restriction in root"
```

---

### Task 3: Relocate Operations-Inbox-specific dead-code list

**Files:**
- Modify: `CLAUDE.md:144-159` (the "Pay special attention to" list)
- Modify: `.claude/rules/operations-inbox.md` (append new section)

- [ ] **Step 1: Edit `CLAUDE.md` lines 144-159**

Replace:
```
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
```

With:
```
Pay special attention to:

Root db_client.py
database/db_client.py
Compatibility aliases
Old backup modules
Debug panels
Unused migrations

Operations Inbox-specific dead-code sensitivity (pages_app/operations_inbox.py, services/operations_inbox_service.py, email-sync/parser/attachment/pending-draft/conversation-history/load-matching code) lives in `.claude/rules/operations-inbox.md`.
```

- [ ] **Step 2: Append to `.claude/rules/operations-inbox.md`** (end of file, after `#Required Reading`)

```markdown

#Dead-Code Sensitivity

Pay special attention before deleting code in this area:

pages_app/operations_inbox.py
services/operations_inbox_service.py
Email-sync services
Parser services
Attachment services
Pending draft code
Conversation-history code
Load-matching code
```

- [ ] **Step 3: Verify nothing was lost — diff the two lists against the original 14-item list**

Run: `grep -c "operations_inbox.py\|Email-sync services\|Parser services\|Attachment services\|Pending draft code\|Conversation-history code\|Load-matching code" CLAUDE.md .claude/rules/operations-inbox.md`
Expected: every item appears exactly once across the two files combined.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/operations-inbox.md
git commit -m "chore: move Operations-Inbox dead-code list to its path-scoped rule file"
```

---

### Task 4: Fix broken code fence and condense Business Context

**Files:**
- Modify: `CLAUDE.md:27-61`

- [ ] **Step 1: Close the unclosed fence and condense Business Context**

Replace lines 27-61:
```
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
```

With:
```
Before editing code, confirm:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git log --oneline -10
```

#Business Context

CaliTrans is a small drayage and transportation company: 10-20 drivers, one dispatcher, one manager, one accounting representative.

The TMS supports: import/export/local-import/local-export container moves, warehouse-to-warehouse transportation, new bookings and booking updates, terminal/gate changes, appointment and PIN requests, driver and delivery status updates, POD and document requests, billing readiness, and customer email communication.

Favor reliability, maintainability, clear workflows, and low operating cost over unnecessary enterprise complexity. The system supports English and Spanish customer communication.
```

- [ ] **Step 2: Verify the fence is balanced and no business content was dropped**

Run: `grep -c '```' CLAUDE.md`
Expected: even number.

Run: `grep -o "10-20 drivers\|one dispatcher\|one manager\|one accounting\|import.*container\|export.*container\|warehouse-to-warehouse\|new bookings\|booking updates\|terminal.*gate\|appointment.*PIN\|driver.*delivery.*status\|POD\|billing readiness\|customer email\|English and Spanish" CLAUDE.md | wc -l`
Expected: all topics still present (count > 0, spot-check against original 13-item list manually).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "fix(docs): close unterminated code fence, condense Business Context without losing content"
```

---

### Task 5: Fix the dead PostToolUse hook

**Files:**
- Modify: `.claude/settings.local.json`

**Context:** Confirmed root cause by direct reproduction: `node .claude/skills/impeccable/scripts/hook.mjs` → `Error: Cannot find module ... MODULE_NOT_FOUND`. `.claude/skills/` does not exist in this repo; `impeccable` is not an installed plugin or cached skill anywhere under `~/.claude` (searched, zero hits). Not a security hook — its own `statusMessage` ("Checking UI changes") and description ("Impeccable design detector... surfaces findings as system reminders") identify it as a UI-design lint helper, not a security control. No source exists to repair it from. Per instruction: repair if useful, else remove only the obsolete hook, preserve security hooks. There are no other hooks in this file to preserve.

- [ ] **Step 1: Remove the dead hook block, keep the rest of the file intact**

Replace the full file content:
```json
{
  "permissions": {
    "allow": [
      "Bash(python -m compileall -q pages_app/dispatch_board.py)",
      "Bash(python -m pytest -q)",
      "Bash(git add *)",
      "Bash(python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core)",
      "Bash(git commit -m ' *)",
      "Bash(python -m pytest -q tests/test_email_client_sync.py)",
      "Bash(claude plugin *)",
      "Bash(python -m pytest -q tests/test_operations_email_sync_budget.py)",
      "Bash(python -m compileall -q services/operations_inbox_service.py services/email_client.py)",
      "Bash(python -c ' *)"
    ]
  },
  "worktree": {
    "baseRef": "head"
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PROJECT_DIR}/.claude/skills/impeccable/scripts/hook.mjs\"",
            "timeout": 5,
            "statusMessage": "Checking UI changes"
          }
        ]
      }
    ]
  },
  "description": "Impeccable design detector: runs after Edit/Write/MultiEdit on UI files and surfaces findings as system reminders."
}
```

With:
```json
{
  "permissions": {
    "allow": [
      "Bash(python -m compileall -q pages_app/dispatch_board.py)",
      "Bash(python -m pytest -q)",
      "Bash(git add *)",
      "Bash(python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core)",
      "Bash(git commit -m ' *)",
      "Bash(python -m pytest -q tests/test_email_client_sync.py)",
      "Bash(claude plugin *)",
      "Bash(python -m pytest -q tests/test_operations_email_sync_budget.py)",
      "Bash(python -m compileall -q services/operations_inbox_service.py services/email_client.py)",
      "Bash(python -c ' *)"
    ]
  },
  "worktree": {
    "baseRef": "head"
  }
}
```

- [ ] **Step 2: Verify the hook no longer fires**

Run: a trivial `Edit` on any file in this plan's task set, observe no "Checking UI changes" / module-not-found error in tool output.
Expected: clean edit, no hook error.

- [ ] **Step 3: Commit**

```bash
git add .claude/settings.local.json
git commit -m "chore: remove dead impeccable PostToolUse hook (target script never existed, MODULE_NOT_FOUND on every edit)"
```

---

### Task 6: Frontend pointer — path-scoped rule + CLAUDE.md fix

**Files:**
- Create: `.claude/rules/frontend-web.md`
- Modify: `web/CLAUDE.md`

**Context:** Confirmed `web/AGENTS.md` is pure `next dev`-generated boilerplate (its own text says so) with zero project content. Confirmed real project-specific content already exists in `web/README.md` (setup/scripts/layout, and the `lib/api/generated.ts` never-hand-edit warning) and `docs/architecture/WEB_CLIENT.md`, just not linked from `web/CLAUDE.md`. Not replacing `web/AGENTS.md` — adding the missing pointer and surfacing the generated-file warning as an always-loaded-when-in-`web/` instruction instead of only prose in a README a human might not read.

- [ ] **Step 1: Write `.claude/rules/frontend-web.md`**

```markdown
---
paths:
  - "web/**"
---

# Frontend (Next.js web client)

Phase 10 foundation — see `docs/architecture/WEB_CLIENT.md` for the full
architecture and `web/README.md` for setup/scripts/layout. Streamlit
remains the system of record for every workflow except the read-only
Loads screen this client builds.

`web/lib/api/generated.ts` is generated from the live FastAPI OpenAPI
schema — never hand-edit it. After any FastAPI route or schema change, run
`npm run api:generate` (from `web/`) to regenerate it.
```

- [ ] **Step 2: Edit `web/CLAUDE.md`**

Replace:
```
@AGENTS.md
```

With:
```
See `docs/architecture/WEB_CLIENT.md` for the Phase 10 architecture and
`web/README.md` for setup/scripts/layout. `lib/api/generated.ts` is
generated — never hand-edit; regenerate via `npm run api:generate` after
any FastAPI route/schema change (see `.claude/rules/frontend-web.md`).

@AGENTS.md
```

- [ ] **Step 3: Verify the auto-managed import survives**

Run: `grep -n "@AGENTS.md" web/CLAUDE.md`
Expected: still present (so `next dev` doesn't re-add a duplicate).

- [ ] **Step 4: Commit**

```bash
git add .claude/rules/frontend-web.md web/CLAUDE.md
git commit -m "docs(web): point CLAUDE.md at real project docs, surface generated-file warning as a loaded rule"
```

---

### Task 7: `docs/superpowers/plans/INDEX.md`

**Files:**
- Create: `docs/superpowers/plans/INDEX.md`

**Context:** Satisfies requirement to preserve `docs/superpowers/plans/` as the canonical planning system — this file lives inside it, not a new directory.

- [ ] **Step 1: Write the index**

```markdown
# Plans Index

One line per plan, grouped by feature area. Check here before writing a
new plan — it may already exist. Add a line here whenever a new plan is
saved to this directory.

## Dispatch Board

- `2026-07-12-dispatch-board-lane-redesign.md` — Lane redesign (Phase 4, part 2)
- `2026-07-12-dispatch-board-status-tab-wiring.md` — Status tab wiring (Phase 4, part 1)
- `2026-07-12-dispatch-transition-service.md` — Transition service (Phase 3)
- `2026-07-13-dispatch-board-row-layout.md` — Row layout & shared canonical stages
- `2026-07-13-dispatch-board-workspace-consolidation.md` — Filter/KPI cleanup & workspace consolidation
- `2026-07-13-booking-card-view-model.md` — Booking card view model (Phase 2)

## Global Shell

- `2026-07-12-global-shell-redesign.md` — Global shell & design token redesign

## Multi-Container Booking

- `2026-07-12-multi-container-booking-grouping.md` — Multi-container booking row grouping
- `2026-07-24-case-007-container-quantity-mismatch.md` — CASE-007 container quantity mismatch

## Orders / Ready-to-Dispatch

- `2026-07-15-orders-management-ready-to-dispatch.md` — Ready to Dispatch step
- `2026-07-15-ready-to-dispatch-sms.md` — Send dispatch text on Ready to Dispatch

## Communications

- `2026-07-16-communications-engine-foundation.md` — Communications engine, Phase 1 foundation

## Email / Case Fixes

- `2026-07-24-case-010-multi-order-email-split.md` — CASE-010 multi-order email split

## Tooling

- `2026-11-12-claude-workflow-optimization.md` — this plan
```

- [ ] **Step 2: Verify count matches directory contents**

Run: `ls docs/superpowers/plans/*.md | grep -v INDEX.md | wc -l`
Expected: 14 (13 existing + this plan itself), matching the 14 entries listed above.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/INDEX.md
git commit -m "docs: add plans index for O(1) prior-art lookup"
```

---

### Task 8: `tms-feature` skill

**Files:**
- Create: `.claude/skills/tms-feature/SKILL.md`

**Context:** Must not duplicate superpowers instructions (per approval condition #6). This skill is a thin, project-specific layer: canonical-location pointers (verified against actual code in this session, not guessed) + the exact `#Required Development Workflow` commands + a hand-off to superpowers skills for planning/TDD/review + the `#First Audit Response` report template. It does not restate what TDD, planning, or review mean.

- [ ] **Step 1: Write the skill**

```markdown
---
name: tms-feature
description: Use when starting substantial work on the CaliTrans TMS repo (not a one-line fix) — confirms repo state, points at canonical pipeline locations instead of re-discovering them, runs the CLAUDE.md audit workflow, and hands off to the right superpowers skill for planning/TDD/review.
---

# TMS Feature Workflow

This skill does not replace superpowers:writing-plans, superpowers:test-driven-development,
superpowers:executing-plans, or superpowers:systematic-debugging — it hands off to them at the
right point instead of restating what they do. It only adds the project-specific parts: where
things already live in this repo, and the exact report shape CLAUDE.md requires.

## 1. Confirm repo state (CLAUDE.md `#Active Repository`)

```powershell
git rev-parse --show-toplevel
git branch --show-current
git status --short
git log --oneline -10
```

Only `C:\GitHub\calitrans_tms_postgres_upgrade_clean`. Never the obsolete
`C:\Users\vreza\OneDrive\Documents\GitHub\calitrans_tms_postgres_upgrade`.

## 2. Canonical locations — verified, don't re-grep these

- Live/historical classification: `classify_message()` in `services/inbox_backfill_policy.py`.
- Business-conversation-key: `_business_conversation_key_from_context()` in `services/operations_inbox_service.py`.
- Pending-order-draft merge, work-item completion, multi-container child-load creation: not
  independently verified at skill-write time — before adding new logic for any of these, run
  `grep -n "def " services/operations_inbox_service.py` first and confirm against
  `CLAUDE.md`'s Architecture Rules list (one canonical function per concern) rather than assuming
  a name or adding a second implementation.
- Operations-Inbox domain rules (classification precedence, multi-container, parsing precedence,
  pending-draft states, conversation rules, email sync budget): `.claude/rules/operations-inbox.md`
  — already auto-loads when touching matching files, don't restate it here.
- Database/worker/script safety detail: `.claude/rules/database-safety.md` — same, auto-loads.
- Frontend (`web/**`): `.claude/rules/frontend-web.md` + `docs/architecture/WEB_CLIENT.md` +
  `web/README.md`.
- Prior implementation plans: `docs/superpowers/plans/INDEX.md` — check before planning new work.

## 3. Compile + targeted test (CLAUDE.md `#Required Development Workflow` / `#Standard Commands`)

```bash
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
python -m pytest -q -k "<keyword(s) matching the touched area>"
```

Markers registered in `pytest.ini` (`inbox`, `attachments`, `backfill`, `worker`, `api`) are
available for any test file that opts in; most existing tests don't yet (introduced gradually,
not retrofitted) — fall back to `-k` keyword targeting until a marker is tagged on the relevant
tests.

## 4. Hand off for planning / implementation

- Multi-step or architecturally non-trivial → invoke `superpowers:writing-plans` (save under
  `docs/superpowers/plans/`, check `INDEX.md` first for existing coverage).
- New feature or bugfix → invoke `superpowers:test-driven-development`.
- Executing an existing plan → invoke `superpowers:executing-plans` or
  `superpowers:subagent-driven-development`.
- Unexplained failure → invoke `superpowers:systematic-debugging` before proposing a fix.

Before deleting anything, follow CLAUDE.md `#Code Review and Dead-Code Rules` in full (search
imports/call sites/aliases/`getattr`/Streamlit callback keys/routers/tests/config; classify;
delete only confirmed-dead or safely-consolidated duplicates).

## 5. Full suite + report (CLAUDE.md `#First Audit Response`)

```bash
python -m pytest -q
```

Report, in this order, every time:

1. Current repository root and branch
2. Git status
3. Compile result
4. Test result (targeted, then full)
5. Top critical findings
6. Confirmed duplicate/dead-code candidates
7. Relevant data-flow summary for the area touched
8. Database/migration risks, if any
9. Proposed sequence / remaining work
10. Changes requiring approval before proceeding

Wait for approval before broad refactoring or destructive database work.
```

- [ ] **Step 2: Verify the skill is discoverable**

Run: start a fresh Claude Code turn in this repo and check the skill appears in the
available-skills system reminder as `tms-feature`.
Expected: listed, with the description above.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/tms-feature/SKILL.md
git commit -m "feat(tooling): add tms-feature skill — points at canonical locations, hands off to superpowers"
```

---

### Task 9: Full verification

- [ ] **Step 1: Line-count delta**

Run: `git show HEAD~8:CLAUDE.md | wc -l` (pre-change) vs `wc -l CLAUDE.md` (post-change) — adjust
`~8` to however many commits this plan produced.

- [ ] **Step 2: Markdown fence balance across all touched docs**

Run: `for f in CLAUDE.md .claude/rules/*.md web/CLAUDE.md; do echo "$f: $(grep -c '```' "$f")"; done`
Expected: every count is even.

- [ ] **Step 3: Full compile + full suite**

Run: `python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core application api scripts`
Expected: clean, no output.

Run: `python -m pytest -q`
Expected: 1512 passed, 110 skipped (same as the pre-change baseline — this plan touches zero
application code or test files).

- [ ] **Step 4: Report**

Per this plan's parent conversation: files changed, before/after CLAUDE.md size, test results,
hook status, estimated token savings. Do not merge, do not push past the dedicated branch.
