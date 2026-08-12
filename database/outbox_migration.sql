-- Calitrans TMS: transactional outbox (Phase 6)
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Durable record of an external side effect (SMS today; email, Motive,
-- webhooks later) that must be enqueued in the SAME database transaction
-- as the business-state write it accompanies, so a rollback of one rolls
-- back the other - see repositories/outbox_repo.py::enqueue_outbox_event
-- and services/outbox_processor.py for the write/claim/process lifecycle.
--
-- idempotency_key is unique so a caller can safely enqueue the same
-- logical event twice (e.g. a retried command after a network timeout on
-- the first attempt) without creating a duplicate outbox row - the second
-- insert is a no-op (see enqueue_outbox_event's ON CONFLICT DO NOTHING).
--
-- status lifecycle: pending -> processing -> delivered
--                                          -> pending (retry, available_at pushed out)
--                                          -> failed (attempt_count reached max, terminal)
--
-- claimed_at is set when a row transitions to 'processing' and cleared on
-- every terminal/requeue transition - repositories/outbox_repo.py::
-- reclaim_stuck_processing uses it (not created_at - a row can sit
-- 'pending' for a long time across retries before being claimed, so
-- created_at is stale by the time of the claim it's supposed to bound)
-- to recover events whose worker crashed after claiming but before
-- recording a result, so nothing stays 'processing' forever.
create table if not exists outbox_events (
    id bigserial primary key,
    event_type text not null,
    aggregate_type text not null,
    aggregate_id text not null,
    payload jsonb not null default '{}'::jsonb,
    idempotency_key text not null unique,
    status text not null default 'pending',
    attempt_count integer not null default 0,
    available_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    claimed_at timestamptz,
    processed_at timestamptz,
    last_error text,
    actor text
);

create index if not exists idx_outbox_events_claimable on outbox_events (status, available_at);
create index if not exists idx_outbox_events_aggregate on outbox_events (aggregate_type, aggregate_id);
