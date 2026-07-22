-- Calitrans TMS Operations Inbox fast-triage columns migration
-- Run this in Supabase SQL Editor after database/order_intake_migration.sql.
-- Safe to run more than once.
--
-- Adds the fast-triage columns to order_intake that make triage filtering
-- and dashboards fast without re-parsing parsed_data._fast_triage on every
-- query. These columns are also applied automatically at runtime by
-- ensure_operations_fast_triage_schema() in services/operations_inbox_service.py,
-- so an already-running app self-heals without this file. This file exists
-- so a database provisioned fresh from the .sql files alone (without ever
-- running the app) already has the correct schema, per CLAUDE.md's
-- requirement that migrations be versioned and committed rather than
-- existing only as runtime column checks.

alter table order_intake add column if not exists triage_status text not null default 'Not Triaged';
alter table order_intake add column if not exists triage_engine text;
alter table order_intake add column if not exists triage_reason text;
alter table order_intake add column if not exists triage_tags jsonb not null default '[]'::jsonb;
alter table order_intake add column if not exists triaged_at timestamptz;
alter table order_intake add column if not exists work_level text;
alter table order_intake add column if not exists department_lane text;
alter table order_intake add column if not exists work_queue text;
alter table order_intake add column if not exists llm_review_required boolean not null default false;
alter table order_intake add column if not exists llm_review_reason text;

create index if not exists idx_order_intake_triage_status on order_intake(triage_status);
create index if not exists idx_order_intake_work_level on order_intake(work_level);
create index if not exists idx_order_intake_work_queue on order_intake(work_queue);
create index if not exists idx_order_intake_llm_review_required on order_intake(llm_review_required);
