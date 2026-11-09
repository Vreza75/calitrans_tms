-- operations_ai_feedback_idempotency_migration.sql
--
-- Adds a deterministic idempotency key to operations_ai_feedback so the
-- dispatcher feedback writer (services/operations_inbox_service.py::
-- save_operations_ai_feedback) can INSERT ... ON CONFLICT DO NOTHING and a
-- Streamlit rerun / accidental double-click records one correction once.
--
-- The operations_ai_feedback table itself is created by
-- operations_email_workflow_migration.sql (applied earlier in
-- scripts/run_migrations.py::MIGRATION_ORDER). This migration only ALTERs
-- that existing table, so it depends on operations_email_workflow_migration.sql.
--
-- Idempotent: safe to re-run against an already-migrated database.

alter table operations_ai_feedback
    add column if not exists idempotency_key text;

-- Unique index backs the ON CONFLICT (idempotency_key) inference in the
-- writer. The key is always computed non-null by the application, so the
-- standard "NULLs are distinct" behaviour does not weaken dedupe in
-- practice; any legacy NULL rows (there are none today - the writer was
-- previously a no-op) remain allowed.
create unique index if not exists uq_operations_ai_feedback_idempotency_key
    on operations_ai_feedback(idempotency_key);
