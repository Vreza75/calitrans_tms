-- Calitrans TMS: AI usage/cost logging table
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- ai_core/usage_logger.py::log_ai_usage() - called on every real LLM call
-- in the app - writes to ai_usage_log via ensure_ai_usage_table(), which
-- runs this exact CREATE TABLE at runtime on every single call (no
-- column_exists()/readiness-cache guard, unlike the other ensure_*_schema()
-- functions elsewhere in this repo). Unlike those, this table previously
-- had no committed migration at all - a database provisioned fresh from
-- database/*.sql alone (without ever running the app) would be missing it
-- entirely, and scripts/verify_schema.py had no expectation to check it
-- against. This file closes that gap, following the same pattern already
-- established for operations_fast_triage_migration.sql and
-- communications_foundation_migration.sql (mirror the runtime DDL into a
-- versioned migration; the runtime self-heal call is left in place, not
-- removed, consistent with that existing pattern).
--
-- Note: repositories/ai_usage_repo.py defines a similarly-named but
-- distinct `ai_usage` table (plural columns, several indexes) with zero
-- callers anywhere in the repo - a pre-existing, separately-documented
-- wiring bug (see docs/DATABASE_MIGRATIONS.md, "What changed in this
-- pass") where pages_app/ai_usage_dashboard.py reads from the dead table
-- instead of this live one. Not addressed here - out of scope for a
-- runtime-DDL migration pass.

create table if not exists ai_usage_log (
    id bigserial primary key,
    created_at timestamptz not null default now(),
    task text,
    agent_name text,
    model text,
    ok boolean,
    input_tokens integer default 0,
    output_tokens integer default 0,
    total_tokens integer default 0,
    estimated_cost_usd numeric(12, 6) default 0,
    latency_seconds numeric(12, 3) default 0,
    error text,
    metadata jsonb not null default '{}'::jsonb
);
