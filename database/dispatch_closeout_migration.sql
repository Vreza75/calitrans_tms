-- Calitrans TMS Dispatch Closeout migration
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Splits billing/closeout progress out of loads.status (which becomes
-- operational-only) into its own independent column, so a load can be
-- Dispatch Complete while closeout is still POD Needed.

alter table loads add column if not exists closeout_stage text not null default 'Not Started';

create index if not exists idx_loads_closeout_stage on loads(closeout_stage);

-- ============================================================
-- ROLLBACK
-- ============================================================
-- drop index if exists idx_loads_closeout_stage;
-- alter table loads drop column if exists closeout_stage;
