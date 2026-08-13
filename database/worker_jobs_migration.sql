-- Calitrans TMS: durable worker job queue (Phase 7)
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Distinct from outbox_events (Phase 6): outbox represents "a business
-- transaction committed; an external side effect must happen" (SMS,
-- future email/Motive/webhooks). worker_jobs represents "internal
-- asynchronous work must be performed" (mailbox sync, message
-- processing) - no external provider involved, no business transaction
-- necessarily preceding it. Same proven schema shape as
-- outbox_migration.sql (claim/retry/backoff/reclaim), deliberately not
-- reused as the same table - see docs/architecture/WORKER_RUNTIME.md.
--
-- idempotency_key is unique so re-requesting the same logical job (e.g. a
-- retried "Sync Inbox" click, or the same provider message id arriving
-- twice) is a no-op, not a duplicate row - see
-- repositories/worker_job_repo.py::enqueue_job's ON CONFLICT DO NOTHING.
--
-- status lifecycle: pending -> processing -> completed
--                                          -> pending (retry, available_at pushed out)
--                                          -> failed (attempt_count reached max, terminal)
--
-- claimed_at is set when a row transitions to 'processing' and cleared on
-- every terminal/requeue transition - repositories/worker_job_repo.py::
-- reclaim_stuck_processing uses it (not created_at - a row can sit
-- 'pending' for a long time across retries before being claimed, so
-- created_at is stale by the time of the claim it's supposed to bound),
-- same reasoning as outbox_events.
create table if not exists worker_jobs (
    id bigserial primary key,
    job_type text not null,
    aggregate_type text not null,
    aggregate_id text not null,
    payload jsonb not null default '{}'::jsonb,
    idempotency_key text not null unique,
    status text not null default 'pending',
    attempt_count integer not null default 0,
    available_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    last_error text,
    actor text
);

create index if not exists idx_worker_jobs_claimable on worker_jobs (status, available_at);
create index if not exists idx_worker_jobs_aggregate on worker_jobs (aggregate_type, aggregate_id);
