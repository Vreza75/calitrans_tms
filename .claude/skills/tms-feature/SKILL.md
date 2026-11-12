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
  pending-draft states, conversation rules, email sync budget, dead-code sensitivity):
  `.claude/rules/operations-inbox.md` — already auto-loads when touching matching files, don't
  restate it here.
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

Follow the installed Superpowers 6.1.1 prerequisites in order — do not skip a stage or jump
straight to a plan or to code:

- **New substantial feature** → `superpowers:brainstorming` first. Get design approval. Only
  then → `superpowers:writing-plans` (save under `docs/superpowers/plans/`, check `INDEX.md`
  first for existing coverage) → execution via `superpowers:executing-plans` or
  `superpowers:subagent-driven-development`.
- **Bug investigation** → `superpowers:systematic-debugging` first. Only once root cause is
  established → `superpowers:test-driven-development` (failing test that reproduces the root
  cause, then the fix) → implementation.
- **Executing an already-approved plan** (brainstorming/writing-plans already done) → invoke
  `superpowers:executing-plans` or `superpowers:subagent-driven-development` directly.

Before deleting anything, follow CLAUDE.md `#Code Review and Dead-Code Rules` in full (search
imports/call sites/aliases/`getattr`/Streamlit callback keys/routers/tests/config; classify;
delete only confirmed-dead or safely-consolidated duplicates).

Before reporting any of the above complete, run `superpowers:verification-before-completion` —
this applies regardless of which path above was taken.

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
