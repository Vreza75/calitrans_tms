# Database Migrations

This app's Postgres/Supabase database is provisioned from the SQL files
under `database/*.sql`. There is no `migrations/` subfolder and the files
have no numeric ordering prefix — the true apply order comes from real
FK/table dependencies between them, encoded as `MIGRATION_ORDER` in
`scripts/run_migrations.py`. That list is the single source of truth for
order; `scripts/verify_schema.py` parses the same files to check a live
database against them, so the two scripts can't drift apart from each
other, but either can drift from `database/*.sql` if a file is edited
without updating `MIGRATION_ORDER` — `tests/test_migration_runner.py`
catches that (`test_every_sql_file_on_disk_is_in_migration_order`).

## Authoritative migration order

1. `schema.sql` — customers, warehouses, carriers, drivers, loads, status_events, documents
2. `portpro_style_migration.sql` — ALTERs loads; creates appointments, tasks
3. `order_intake_migration.sql` — order_intake
4. `port_houston_integration_migration.sql` — port_houston_sync_log
5. `operations_email_workflow_migration.sql` — ALTERs order_intake; creates operations_cases, operations_case_notes, operations_case_owner_history, operations_case_events, load_communications, quote_requests, dispatch_messages, email_notifications, operations_email_replies, operations_ai_feedback, operations_inbox_preferences
6. `dispatcher_workspace_migration.sql` — operations_tasks, dispatcher_actions, ai_recommendation_decisions
7. `drivers_master_data_migration.sql` — ALTERs drivers
8. `dispatch_closeout_migration.sql` — ALTERs loads (closeout_stage)
9. `company_memory.sql` — company_memory
10. `multi_container_migration.sql` — order_intake_drafts; ALTERs loads (parent_booking_key, container_sequence, etc.)
11. `communications_foundation_migration.sql` — ALTERs dispatch_messages
12. `operations_fast_triage_migration.sql` — ALTERs order_intake (triage_status, work_queue, etc.)
13. `loads_source_intake_idempotency_migration.sql` — ALTERs loads (adds `source_intake_id`, FK -> order_intake) and adds a partial unique index (`ux_loads_source_intake_id`, where not null) so a retried inbox-to-load creation resolves to the existing load instead of inserting a duplicate
14. `ai_usage_log_migration.sql` — creates `ai_usage_log` (the table `ai_core/usage_logger.py::log_ai_usage()` actually writes to on every real LLM call). Previously had **no migration coverage at all** - the runtime `ensure_ai_usage_table()` call was the only definition of this table's schema anywhere, and it runs unconditionally on every call (no readiness-cache guard, unlike the other `ensure_*_schema()` functions below)
15. `app_users_migration.sql` — creates `app_users` (Streamlit + API login: email, bcrypt password hash, role, is_active), with a unique index on `lower(email)` so case-variant emails can't create duplicate accounts. See `docs/AUTHENTICATION.md`.
16. `outbox_migration.sql` — creates `outbox_events` (Phase 6 transactional outbox: durable queue for external side effects like the driver-dispatch SMS, enqueued in the same transaction as the business-state write it accompanies). See `docs/architecture/OUTBOX.md`.

This numbered list mirrors `MIGRATION_ORDER` in `scripts/run_migrations.py` at the
time of writing. When a new migration is appended there, add it here too -
`tests/test_migration_runner.py::test_every_sql_file_on_disk_is_in_migration_order`
catches a file that exists on disk but isn't registered in `MIGRATION_ORDER`,
but nothing enforces that this doc's list stays in sync with that registration,
so treat this file as needing a one-line addition whenever `MIGRATION_ORDER` grows.

**Required rollout order for this pass**: apply this migration (`python scripts/run_migrations.py`) *before* deploying application code that calls `DispatchDatabaseClient.add_row(..., source_intake_id=...)` (currently `services/order_intake.py::create_load_from_intake_with_status`, used by `services/operations_inbox_service.py::create_load_from_inbox_item`). The application does not create this column/index itself - if it runs against a database that hasn't had this migration applied yet, the `INSERT` referencing `source_intake_id` fails with a clear, sanitized `UndefinedColumn`-derived error (via `application/loads/commands.py`'s existing exception sanitization), not a silent schema mutation or a silently-skipped idempotency check. Verify with `python scripts/verify_schema.py` after applying (its index parser now also recognizes `create unique index`, not just `create index` - see `tests/test_migration_runner.py::test_parse_expected_schema_finds_unique_indexes`).

**Rollout order for `outbox_migration.sql`**: apply it *before* deploying the Phase 6 application code (`application/loads/commands.py::mark_load_ready_to_dispatch` calling `repositories/outbox_repo.py::enqueue_outbox_event`) - the reverse order makes that command's outbox insert fail against a nonexistent `outbox_events` table. `outbox_migration.sql` has no FKs, so it can otherwise run any time after `schema.sql`.

Every file is idempotent (`create table/index if not exists`,
`add column if not exists`, `drop trigger if exists` + recreate) so
rerunning the full set is always safe.

## What changed in this pass

`services/operations_inbox_service.py::ensure_operations_fast_triage_schema()`
was creating 10 `order_intake` columns and 4 indexes purely at runtime, with
no committed migration — a database provisioned from `database/*.sql` alone
(without ever booting the app) would be missing them. Added
`database/operations_fast_triage_migration.sql`, mirroring that function's
DDL exactly, and appended it to `MIGRATION_ORDER`.

## Runtime DDL: eliminated

Runtime/request/page/service execution performs **zero schema DDL**. Every
`ensure_*_schema()`/`_ensure_*_table()` self-heal function that used to run
`CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` at runtime has been converted to
a read-only call to `db_client.require_schema_ready(table, column,
migration_hint=...)`, which raises `db_client.SchemaNotReadyError` — a
clear, actionable error naming the exact migration to run — instead of
mutating schema. `tests/test_no_runtime_ddl.py` enforces this with a
repo-wide scanner and no allowlist: the test fails if schema DDL is ever
reintroduced into `services/`, `repositories/`, `application/`, `api/`,
`pages_app/`, `ui_components/`, `ai_core/`, `ai_agents/`, `db_client.py`, or
`app.py`.

This was done in two passes:

**First pass** did a case-insensitive, repo-wide search and classified
every hit. All were confirmed, byte-for-byte, to be complete duplicates or
strict subsets of an already-existing, already-registered migration — each
had been added deliberately (see each migration file's own header comment,
still present, describing the historical rationale) as a self-heal fallback
for an already-running deployment that hadn't had a given migration applied
yet:

- `ensure_operations_fast_triage_schema()` and
  `ensure_operations_email_sync_schema()` (`services/operations_inbox_service.py`)
- `ensure_communications_schema()` (`services/dispatch_data_service.py`) —
  ran on the hot path of every dispatch-message send
- `ensure_operations_case_schema()` (`services/operations_case_service.py`)
  — the migration was already a *superset* of the runtime version (more
  indexes), not the reverse; the "known drift" flagged by an earlier pass
  meant the runtime path was behind the migration, not that the migration
  was incomplete
- `ensure_task_schema()` (`repositories/task_repo.py`) — same shape:
  migration already a superset
- `ensure_company_memory_schema()` (`ai_core/company_memory.py`)
- `_ensure_port_houston_sync_log_table()` (`pages_app/port_houston_integration.py`)
- `ensure_ai_usage_table()` (`ai_core/usage_logger.py`) — ran
  unconditionally on every real LLM call, no readiness-cache guard

**Second pass** covered `repositories/ai_usage_repo.py`'s
`ensure_ai_usage_schema()`/`log_ai_usage_record()`, which were entangled
with the `ai_usage`/`ai_usage_log` wiring fix — see that section below.

No new migration content was needed for any of these: since every runtime
statement was already redundant with an existing migration, removing the
runtime DDL and replacing it with a readiness check was a pure code change,
verified against a real (if not live-Postgres) test for each conversion —
see `tests/test_require_schema_ready.py`.

Two lower-priority items remain, unchanged from an earlier pass and still
out of scope for this one:

- `portpro_style_migration.sql`'s `appointments` and `tasks` tables have no
  Python code reading or writing them anywhere in the repo (confirmed by
  grep). Not deleted per this repo's dead-code rules (requires more than a
  grep pass — Streamlit callback keys, dynamic access, etc. — before any
  removal).
- (Resolved this pass, kept here as history) `ai_core/usage_logger.py::log_ai_usage()`
  writes to table `ai_usage_log`. `repositories/ai_usage_repo.py` used to
  define a similarly-named but distinct `ai_usage` table with a writer that
  had zero callers anywhere — `pages_app/ai_usage_dashboard.py` read from
  that always-empty table instead of the live one. Fixed: `ai_usage_repo.py`
  now reads from `ai_usage_log` directly; the dead `ai_usage` table/schema/
  writer were removed from the codebase (never had a migration, so nothing
  to migrate away from). See "AI usage logging" below.

### Verifying no runtime DDL remains

```bash
pytest tests/test_no_runtime_ddl.py -v
```

This is a permanent regression guard, not a one-time audit — it runs as
part of the normal suite.

## AI usage logging

`ai_usage_log` (`database/ai_usage_log_migration.sql`) is the single
canonical table for AI/LLM usage tracking. `ai_core/usage_logger.py::log_ai_usage()`
is the only writer (called on every real LLM call); `repositories/ai_usage_repo.py`
is the only reader, used by `pages_app/ai_usage_dashboard.py`. Both agree on
the same schema (`task`, `agent_name`, `model`, `ok`, `input_tokens`,
`output_tokens`, `total_tokens`, `estimated_cost_usd`, `latency_seconds`,
`error`, `metadata`, `created_at`) — there is no second table, no
compatibility shim, and no drift between what gets written and what gets
read.

## Running migrations

```bash
python scripts/run_migrations.py                              # uses configured DATABASE_URL
python scripts/run_migrations.py --database-url "postgresql://..."
python scripts/run_migrations.py --dry-run                     # lists what would run, opens no DB connection
```

Applied filenames are recorded in a `schema_migrations` table
(`filename`, `applied_at`). Reruns skip anything already recorded. Each
file runs inside one transaction — PostgreSQL DDL is transactional, so a
failure partway through a file rolls back that file only; every
previously-applied file stays applied. The runner never prints credentials,
only `scheme://***@host:port/dbname`.

## Verifying schema

```bash
python scripts/verify_schema.py
python scripts/verify_schema.py --database-url "postgresql://..."
```

Parses every file in `MIGRATION_ORDER` for its `create table` columns,
`alter table add column`, `create index`, and `create trigger` statements,
then checks a live database's `information_schema`/`pg_indexes` against
that — so this can never assert something the migrations themselves don't
actually define. Exit code 1 if anything expected is missing.

## Data-integrity report (read-only)

```bash
python scripts/data_integrity_report.py
```

Never deletes or rewrites anything. Checks: orphaned foreign keys (derived
generically from `information_schema` FK constraints, not hardcoded),
duplicate `parent_booking_key`+`container_sequence` pairs, blank required
identifiers (`loads.booking_number`, `drivers.driver_name`), `loads.status`
values not recognized by `services/dispatch_legacy_status.py` /
`services/dispatch_stages.py`, `order_intake.service_flow` values outside
Import/Export/Local Import/Local Export, duplicate
`order_intake.source_message_id`, duplicate `dispatch_messages.provider_message_id`.
Prints a findings list for human review; always exits 0 (it's a report, not
a gate).

## Fresh empty-database verification (run this yourself against a scratch DB)

```bash
createdb calitrans_scratch          # or use your Postgres provider's dashboard
export MIGRATION_TEST_DATABASE_URL="postgresql://.../calitrans_scratch"

python scripts/run_migrations.py --database-url "$MIGRATION_TEST_DATABASE_URL"
python scripts/verify_schema.py --database-url "$MIGRATION_TEST_DATABASE_URL"
python -m pytest -q   # gated migration-runner tests activate automatically
                       # once MIGRATION_TEST_DATABASE_URL is set

# then, pointed at that scratch DB via .env / secrets.toml:
streamlit run app.py
# create one of each through the UI: customer, warehouse, driver,
# single-container load, multi-container booking, order-intake record,
# operations case, communication record, status event — then confirm each
# reads back correctly through its page.
```

## Existing-database clone upgrade verification (run this yourself)

Never run untested migrations directly against the real dev/production
database. Clone first:

```bash
# Backup / clone (see below for the exact pg_dump/restore commands).
# Record row counts before:
psql "$CLONE_DATABASE_URL" -c "select 'loads', count(*) from loads
    union all select 'order_intake', count(*) from order_intake
    union all select 'operations_cases', count(*) from operations_cases
    union all select 'dispatch_messages', count(*) from dispatch_messages;"

python scripts/run_migrations.py --database-url "$CLONE_DATABASE_URL"

# Record row counts again with the same query — they must match (this
# migration set only adds columns/indexes/tables, never drops or rewrites
# rows).
python scripts/verify_schema.py --database-url "$CLONE_DATABASE_URL"
python scripts/data_integrity_report.py --database-url "$CLONE_DATABASE_URL"

# Then, pointed at the clone:
# - Open a few existing loads and confirm they render.
# - Open a few existing Operations Inbox records and confirm they render.
# - Confirm multi-container bookings still group under parent_booking_key.
# - Confirm loads.status still normalizes via dispatch_legacy_status.
# - Confirm dispatch_messages/load_communications history still shows for
#   an existing load.
python -m pytest -q
```

## Idempotency verification

```bash
python scripts/run_migrations.py --database-url "$MIGRATION_TEST_DATABASE_URL"
python scripts/run_migrations.py --database-url "$MIGRATION_TEST_DATABASE_URL"
# second run's output should read "0 newly applied, N already applied, N total"
# where N == len(MIGRATION_ORDER) at the time you run this (see
# scripts/run_migrations.py's final summary line) — and print SKIP for every
# filename. tests/test_migration_runner.py's
# TestMigrationRunnerAgainstARealScratchDatabase class asserts "0 newly
# applied" appears in the second run's output when MIGRATION_TEST_DATABASE_URL
# is set; it deliberately does not hardcode the "already applied" count, so
# this doc doesn't either.
```

## Backup and rollback

This app is hosted on Supabase-managed PostgreSQL. Use the **direct**
connection (port 5432), not the pgbouncer transaction-pooler connection
(port 6543) used at app runtime, for `pg_dump`/`pg_restore` — transaction
pooling can interfere with dump/restore sessions. Get the direct connection
string from the Supabase dashboard (Project Settings → Database) or
substitute it for `$DIRECT_DATABASE_URL` below.

```bash
# Backup / clone source
pg_dump "$DIRECT_DATABASE_URL" -Fc -f calitrans_backup_$(date +%Y%m%d_%H%M%S).dump

# Restore into a *new*, empty database (never restore over a live one in place)
createdb calitrans_clone
pg_restore --clean --if-exists -d "postgresql://.../calitrans_clone" calitrans_backup_*.dump
```

**Rollback**: none of these migrations have a true down migration — they
only add tables/columns/indexes, never drop or rewrite data, so a
hand-written down migration has nothing destructive to undo. If a migration
run ever needs to be rolled back anyway (e.g. an index causing unexpected
load), restore-from-backup is the rollback method:

```bash
# Point the app at nothing, then:
pg_restore --clean --if-exists -d "$DATABASE_URL" calitrans_backup_<timestamp>.dump
# Then manually delete the schema_migrations rows for any migration that
# should be considered "not applied" again after the restore.
```
