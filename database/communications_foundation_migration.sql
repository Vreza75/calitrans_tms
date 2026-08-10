-- Calitrans TMS Communications Engine Phase 1 migration
-- Run this in Supabase SQL Editor after database/schema.sql and
-- database/operations_email_workflow_migration.sql.
-- Safe to run more than once.
--
-- Extends the existing dispatch_messages table with the columns the
-- provider-agnostic communications layer needs (services/communications/).
-- These columns are also applied automatically at runtime by
-- ensure_communications_schema() in services/dispatch_data_service.py, so
-- an already-running app self-heals without this file. This file exists
-- so a database provisioned fresh from the .sql files alone (without ever
-- running the app) already has the correct schema, per CLAUDE.md's
-- requirement that migrations be versioned and committed.
--
-- provider defaults to 'internal' for historical rows, which is accurate:
-- every dispatch_messages row inserted before this migration was an
-- internal note, driver dispatch message, or customer status email sent
-- directly (not through a tracked external provider).

alter table dispatch_messages add column if not exists provider text not null default 'internal';
alter table dispatch_messages add column if not exists delivery_status text;
alter table dispatch_messages add column if not exists read_status text;
alter table dispatch_messages add column if not exists attachments jsonb;
alter table dispatch_messages add column if not exists metadata jsonb;
alter table dispatch_messages add column if not exists provider_message_id text;
