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
12. `operations_fast_triage_migration.sql` — ALTERs order_intake (triage_status, work_queue, etc.) — **new**, see below

Every file is idempotent (`create table/index if not exists`,
`add column if not exists`, `drop trigger if exists` + recreate) so
rerunning the full set is always safe.

## What changed in this pass

`services/operations_inbox_service.py::ensure_operations_fast_triage_schema()`
was creating 10 `order_intake` columns and 4 indexes purely at runtime, with
no committed migration — a database provisioned from `database/*.sql` alone
(without ever booting the app) would be missing them. Added
`database/operations_fast_triage_migration.sql`, mirroring that function's
DDL exactly, and appended it to `MIGRATION_ORDER`. No other schema gaps
were found — every other runtime `ensure_*_schema()` function either exactly
duplicates an existing `.sql` migration (harmless, both idempotent) or is a
thin proxy to one of the above.

Two lower-priority items were found but **not changed** (out of scope for a
schema-reconciliation pass — flagging for a separate decision):

- `repositories/task_repo.py::ensure_task_schema()` and
  `services/operations_case_service.py::ensure_operations_case_schema()`
  recreate tables already defined in `.sql` migrations but omit several of
  those migrations' indexes. Not broken (the `.sql` migrations are complete),
  but means "schema built via the app's runtime path only" is missing
  indexes that "schema built via `run_migrations.py`" has.
- `portpro_style_migration.sql`'s `appointments` and `tasks` tables have no
  Python code reading or writing them anywhere in the repo (confirmed by
  grep). Not deleted per this repo's dead-code rules (requires more than a
  grep pass — Streamlit callback keys, dynamic access, etc. — before any
  removal).
- `ai_core/usage_logger.py::log_ai_usage()` (called by every real LLM call)
  writes to table `ai_usage_log`. `repositories/ai_usage_repo.py::log_ai_usage_record()`
  (the only writer of table `ai_usage`, which is what `pages_app/ai_usage_dashboard.py`
  reads) has zero callers anywhere. The two tables were never reconciled —
  the AI Usage Dashboard will always read empty data. This is an application
  wiring bug, not a schema problem, so it's out of scope here; flagging for
  a separate fix.

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
# second run's output should read "0 newly applied, 12 already applied"
# and print SKIP for every filename — tests/test_migration_runner.py's
# TestMigrationRunnerAgainstARealScratchDatabase class asserts exactly this
# when MIGRATION_TEST_DATABASE_URL is set.
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
