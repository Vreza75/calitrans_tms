-- Calitrans TMS: idempotent load creation from Operations Inbox intake
-- Run this in Supabase SQL Editor after database/schema.sql and
-- database/order_intake_migration.sql.
-- Safe to run more than once.
--
-- Purpose: reprocessing or retrying the same order_intake row must not
-- create a second loads row. order_intake.linked_load_id already points
-- from intake -> load, but nothing on the loads side records which
-- intake row created it, so nothing could stop a retry from inserting a
-- second load. source_intake_id closes that gap: DispatchDatabaseClient
-- .add_row() now inserts it whenever a load is created from an inbox
-- intake record, using `on conflict (source_intake_id) ... do nothing`,
-- so a second attempt for the same intake row is a database-enforced
-- no-op rather than a race-prone application-side check.
--
-- Nullable and unindexed for existing rows: manual/legacy load creation
-- (Streamlit "Add Load", the legacy POST /loads API endpoint) never sets
-- this column, so those rows keep source_intake_id = NULL and are
-- unaffected. The partial index below only constrains rows that do set
-- it. Since this is a brand-new column with no prior data, there are no
-- pre-existing duplicates to check for before adding the index.

alter table loads add column if not exists source_intake_id bigint references order_intake(id) on delete set null;

create unique index if not exists ux_loads_source_intake_id
on loads (source_intake_id)
where source_intake_id is not null;
