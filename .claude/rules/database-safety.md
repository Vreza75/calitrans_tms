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
