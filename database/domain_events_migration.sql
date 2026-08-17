-- Calitrans TMS: durable domain/change event queue (Phase 9)
-- Run this in Supabase SQL Editor after database/schema.sql.
-- Safe to run more than once.
--
-- Distinct from outbox_events (Phase 6) and worker_jobs (Phase 7):
--   outbox_events  = "a business transaction committed; an external side
--                     effect must happen" (SMS, document finalize) - loss
--                     is a business-correctness problem, so it gets
--                     MAX_ATTEMPTS then a terminal 'failed' state an
--                     operator must resolve.
--   worker_jobs    = "internal asynchronous work must be performed"
--                     (mailbox sync, message processing) - no external
--                     provider, no business transaction necessarily
--                     preceding it.
--   domain_events  = "a business transaction committed; connected clients
--                     should be told what changed so they can refetch."
--                     Loss is recoverable BY DESIGN - a client that never
--                     receives a notification (or receives it late) can
--                     always refetch the authoritative REST resource, so
--                     this queue's retry policy is deliberately simpler
--                     and its failures are not operator-escalated the way
--                     outbox failures are. Reusing outbox_events for this
--                     was considered and rejected (see docs/architecture/
--                     REALTIME_EVENTS.md, "Why not the outbox") - the two
--                     have incompatible failure-severity semantics and
--                     conflating them would either weaken the outbox's
--                     guarantee or over-escalate realtime noise.
--
-- Same proven schema/lifecycle shape as outbox_migration.sql (claim/
-- retry/backoff/reclaim) - see repositories/domain_event_repo.py.
--
-- version: caller-supplied staleness signal (e.g. the aggregate's own
-- updated_at, as an ISO string) - lets a client discard an event it
-- knows is older than what it already has. Nullable: not every emitter
-- has a natural version value yet.
--
-- idempotency_key is unique so re-recording the same logical event (a
-- retried command after a network timeout) is a no-op, not a duplicate
-- row - see each emitter's idempotency-key construction for what "same
-- logical event" means for that event type.
--
-- status lifecycle: pending -> processing -> published
--                                          -> pending (retry, available_at pushed out)
--                                          -> failed (attempt_count reached max, terminal)
create table if not exists domain_events (
    id bigserial primary key,
    event_type text not null,
    aggregate_type text not null,
    aggregate_id text not null,
    version text,
    payload jsonb not null default '{}'::jsonb,
    idempotency_key text not null unique,
    status text not null default 'pending',
    attempt_count integer not null default 0,
    available_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    claimed_at timestamptz,
    published_at timestamptz,
    last_error text,
    actor text
);

create index if not exists idx_domain_events_claimable on domain_events (status, available_at);
create index if not exists idx_domain_events_aggregate on domain_events (aggregate_type, aggregate_id);
