# Worker Runtime (Phase 7)

## Status: batch 1 of N - foundation only

This document tracks Phase 7 (Worker Runtime + Operations Inbox
Processing Extraction) as it's built in reviewed batches, not all at
once. What exists after batch 1:

- `database/worker_jobs_migration.sql` - the durable job queue table.
- `repositories/worker_job_repo.py` - framework-neutral persistence
  (enqueue/claim/mark/reclaim/list/retry), same proven shape as
  `repositories/outbox_repo.py`.
- `application/inbox/commands.py::request_inbox_sync` - the one
  user-triggered command that exists so far. Enqueues an `inbox.sync`
  job and returns immediately.
- `tests/test_worker_job_repo.py`, `tests/test_inbox_commands.py`.

What does **not** exist yet, deliberately deferred to later batches:

- **No worker runtime.** `workers/processor.py` is not built. An
  enqueued `inbox.sync` job sits `'pending'` forever right now - nothing
  claims or processes it. `request_inbox_sync` is safe to ship ahead of
  the worker because enqueueing is a no-op until something consumes the
  queue, same as any other durable-queue producer/consumer split.
- **No worker CLI** (`scripts/process_worker_jobs.py`).
- **No email/message processing extraction.** `services/
  operations_inbox_service.py::sync_operations_email_engine` is
  untouched - the Streamlit "Sync Email Engine" button still calls it
  synchronously, exactly as before this batch. STEP 16/28 (make that
  button enqueue-and-return instead) happens once a worker actually
  exists to consume the queue - shipping the button change first would
  make sync silently do nothing.
- **No Inbox query/detail API work.** `GET /api/v1/work-items` and
  `GET /api/v1/work-items/{id}` (`api/routers/work_items.py`,
  `application/work_items/queries.py`) already cover this - Phase 7 does
  not duplicate them under an `application/inbox/queries.py` with
  renamed `Inbox*` models. See "Scope correction" below.
- **No job-status API**, no `inbox.process_message` job type, no system
  actor beyond passing a plain string.

## Worker jobs vs. outbox events

Two distinct concepts, kept in two separate tables/modules - conflating
them was an explicit anti-goal for this phase:

| | `outbox_events` (Phase 6) | `worker_jobs` (Phase 7) |
|---|---|---|
| Represents | A business transaction committed; an external side effect must happen | Internal asynchronous work must be performed |
| Examples | `driver_dispatch_sms`, `document.file.finalize` | `inbox.sync` (built), `inbox.process_message` (not built yet) |
| Triggered by | A business command's own transaction, alongside the state it accompanies | A user request or another job, not necessarily paired with a business-state write |
| External provider involved | Usually (Twilio, filesystem) | Not necessarily |

Same schema shape (`pending -> processing -> completed/delivered`,
`claimed_at`-based stale reclaim, bounded retry with backoff,
`idempotency_key` unique constraint) because the reliability problem is
identical - proven in Phase 6, reused here rather than re-invented.

## Schema

`database/worker_jobs_migration.sql`:

```sql
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
```

`completed_at` (not `processed_at`, unlike `outbox_events`) - matches
this table's own terminal-success status name (`'completed'`, not
`'delivered'`, since there's no external provider confirming delivery
for most job types).

## Idempotency

`inbox.sync`'s key: `inbox.sync:<source>:<60s time bucket>`
(`application/inbox/commands.py::_inbox_sync_idempotency_key`). Unlike
the SMS dedupe key (`application/loads/commands.py::
_driver_dispatch_sms_idempotency_key`), this window exists only to
collapse an accidental double-click into one job - there is no
"permanently suppresses a legitimate future resend" risk to guard
against, because syncing the same mailbox twice is harmless (per-message
dedup already happens inside the sync pipeline itself, unrelated to this
job-level key). A sync request 61 seconds after the last one gets a
fresh job, which is the correct, intended behavior - not a defect the
way a stale SMS key would be.

`_SYNC_SOURCE = "primary_mailbox"` is a constant, not derived from
`config.py`'s `IMAP_*` secrets - this system syncs exactly one
configured mailbox today. If that becomes multi-mailbox, this becomes a
parameter; not built now (YAGNI).

`enqueue_job` returns the job's `id` whether freshly inserted or already
existing for that `idempotency_key` (`ON CONFLICT ... DO UPDATE ...
RETURNING id`, where the "update" is a no-op self-assignment used only
to make `RETURNING` fire on conflict too) - unlike `outbox_repo.
enqueue_outbox_event`, which returns nothing, because no outbox caller
has needed the id back so far. `request_inbox_sync` does need it, to
report which job a sync request resolved to.

## Scope correction from the original Phase 7 brief

The original Phase 7 instructions assumed a green-field `application/
inbox/` package covering models, commands, and queries (`InboxWorkItem`,
`InboxWorkItemSummary`, `InboxWorkItemDetail`, `InboxQueueFilter`, a
`GET /api/v1/inbox` + `GET /api/v1/inbox/{id}` API). That work already
exists, built in an earlier phase under `application/work_items/` +
`api/routers/work_items.py` (`WorkItemSummary`, `WorkItemDetail`,
`FilterMeta`, `WorkItemPage`, paginated/filtered list + detail
endpoints, plus `/attachments`, `/conversation`, `/draft` sub-resources
and `close`/`link-load`/`create-load`/`update-load` commands).
`application/inbox/` does not duplicate any of it - it owns only what's
genuinely new: triggering and (in a later batch) processing the sync
pipeline itself. `application/work_items/` remains the place for
commands that operate on an existing work item.

## System actor

No `Role.SYSTEM`/`ActorType` enum added. `repositories/work_item_repo.py
::insert_case_event`'s `actor: str = "dispatcher"` parameter (and the
equivalent pattern elsewhere) already takes a plain string - every
current caller passes it explicitly. A future worker-originated write
just needs to pass a string like `"system:inbox-worker"` instead of a
human actor's email; no schema or auth-model change required. Not built
in this batch since no worker code writes anything yet.

## Next batches (not built here)

1. `workers/processor.py` (generic claim/dispatch loop, handler
   registry) + `scripts/process_worker_jobs.py` (CLI, same shape as
   `scripts/process_outbox.py`).
2. Extract `sync_operations_email_engine`'s body into an `inbox.sync`
   job handler; convert the Streamlit "Sync Email Engine" button
   (`pages_app/operations_inbox.py:3306`) and the admin debug sync
   (`pages_app/email_imports.py:139`) to enqueue-and-return.
3. `inbox.process_message` job type + message-processing extraction
   (STEP 18) - parse, CASE-010 segment, classify, match, attach,
   persist, reusing existing algorithms unchanged.
