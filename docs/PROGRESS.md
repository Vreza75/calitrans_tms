# Progress Log

Chronological, most recent first. Only entries where implementation status
genuinely changed — not a running diary.

## 2026-07-24 — Operations Inbox certification: 10/11 cases accepted

- Built the certification harness (`tests/integration/operations_inbox/`,
  `scripts/run_inbox_case.py`) — runs a fixture email through the real
  production intake pipeline against a disposable scratch Postgres DB.
- Certified CASE-000 through CASE-009 (10 fixtures): all ACCEPTED. Each
  found and fixed a genuine pre-existing classification/parsing defect.
- CASE-007 (container quantity mismatch) required building a real
  capability (multi-container-number extraction + declared-vs-found
  mismatch detection/routing) — done via a full brainstorm → spec → plan →
  subagent-driven-implementation cycle. Now ACCEPTED.
- CASE-010 (two separate orders in one email) also requires a real
  capability (multi-order detection/splitting) — audited, documented,
  **not yet built**. See `docs/handoffs/CURRENT_SESSION_HANDOFF.md` for the
  exact next steps.
- Full test suite: 306 passed, 42 skipped (skips are the certification
  suite, gated behind an opt-in `INBOX_CERTIFICATION_DATABASE_URL` env var
  so a normal `pytest -q` run never touches a real database).
- Full details, file list, and commit list: `docs/handoffs/CURRENT_SESSION_HANDOFF.md`.

## Earlier (pre-existing, before this session)

- `7f51cff` — deterministic migration runner; closed an `order_intake`
  schema gap.
- `b036f67` — fixed `data_integrity_report` checking `service_flow` on the
  wrong table.
- Earlier history: see `git log` — this file starts tracking from the
  Operations Inbox certification effort onward; it does not retroactively
  document everything before it.
