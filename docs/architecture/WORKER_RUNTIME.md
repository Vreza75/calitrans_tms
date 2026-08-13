# Worker Runtime (Phase 7)

## Status: batch 4 of N - message-processing pipeline is addressable, no producer yet

This document tracks Phase 7 (Worker Runtime + Operations Inbox
Processing Extraction) as it's built in reviewed batches, not all at
once. What exists after batch 2:

- `database/worker_jobs_migration.sql` - the durable job queue table.
- `repositories/worker_job_repo.py` - framework-neutral persistence
  (enqueue/claim/mark/reclaim/list/retry), same proven shape as
  `repositories/outbox_repo.py`.
- `application/inbox/commands.py::request_inbox_sync` - the one
  user-triggered command that exists so far. Enqueues an `inbox.sync`
  job and returns immediately.
- `workers/processor.py` - generic claim/dispatch/retry loop
  (`process_one`, `process_pending`), same proven shape as
  `services/outbox_processor.py`.
- `workers/inbox_handlers.py::handle_inbox_sync` - the first registered
  handler, for `job_type='inbox.sync'`. Wraps `services.
  operations_inbox_service::sync_operations_email_engine` **unchanged**
  - no rewrite, same defaults (limit 12, 25s budget), same classification/
  parsing/matching pipeline. A worker running `python scripts/
  process_worker_jobs.py process` now actually processes the `inbox.sync`
  jobs `request_inbox_sync` enqueues.
- `scripts/process_worker_jobs.py` - operator CLI (`process`,
  `list-pending`, `list-failed`, `inspect`, `retry`, `retry-all`), same
  shape as `scripts/process_outbox.py`.
- `workers/inbox_handlers.py::handle_inbox_process_message` -
  `job_type='inbox.process_message'`, registered but **not yet enqueued
  by anything** (no producer exists). Wraps `services.
  operations_inbox_service::_insert_operations_email_message` unchanged -
  the exact function `sync_operations_email_engine`'s own per-message
  loop already calls today. This function already covers STEP 18's full
  pipeline in the right order (latest-body extraction + CASE-010
  segmentation, parse, attachment processing, classification/triage,
  load matching, persist) as a single already-framework-neutral,
  already-DB-write-isolated call (`_prepare_operations_email_record` is
  explicitly documented in its own docstring as "the pure (DB-write-free)
  half" - `_insert_operations_email_record_row` is the persist half) -
  STEP 18 did not require building new orchestration, only recognizing
  and exposing what already existed as a worker-addressable job type.
- `tests/test_worker_job_repo.py`, `tests/test_inbox_commands.py`,
  `tests/test_worker_processor.py`, `tests/test_process_worker_jobs_cli.py`,
  `tests/test_inbox_handlers.py`.

What does **not** exist yet, deliberately deferred to later batches:

- **Nothing runs the worker automatically.** No cron/Task Scheduler/
  always-on process invokes `scripts/process_worker_jobs.py process`
  anywhere in this repo or its deployment config. `inbox.sync` jobs are
  enqueued and (once something runs the CLI) correctly processed, but
  nothing does that on its own yet - an operator runs it manually today.
- **The Streamlit "Sync Email Engine" button is untouched.** It still
  calls `sync_operations_email_engine` synchronously, exactly as before
  Phase 7 (`pages_app/operations_inbox.py:3306`,
  `pages_app/email_imports.py:139`). Converting it to enqueue-and-return
  (STEP 16/28) was deliberately deferred - doing that before a periodic
  worker runner exists would make the live "Sync Email Engine" button
  silently stop doing anything. Batch 3 proves the worker *can* run the
  real pipeline (via the CLI, manually), without changing what
  dispatchers experience today.
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

`inbox.process_message`'s intended key (no producer builds this yet):
`inbox.process_message:<provider-message-id>`, where the message id is
`services.operations_inbox_service::_email_sync_unique_message_id(message)`
- the same id `_insert_operations_email_message` already computes
internally for its own dedup logic. One message, one job, forever - same
reasoning as Phase 6B's `document_finalize:{document_id}` key, not the
SMS-style time-bucketed key, since a provider message id is a stable,
pre-existing unique identity, not content that could legitimately repeat.

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

1. Decide and configure an actual periodic worker runner (cron/Task
   Scheduler/always-on process/Streamlit Cloud-compatible mechanism -
   deployment infrastructure was never confirmed, same open question
   Phase 6B's storage-durability section flagged) before converting the
   Streamlit button - otherwise the conversion breaks a live daily-use
   feature.
2. Convert the Streamlit "Sync Email Engine" button
   (`pages_app/operations_inbox.py:3306`) and the admin debug sync
   (`pages_app/email_imports.py:139`) to enqueue-and-return via
   `request_inbox_sync`, once (1) is in place.
3. Give `inbox.process_message` a real producer: split
   `sync_operations_email_engine`'s inline per-message loop
   (services/operations_inbox_service.py:4929-4991) into "fetch and
   persist raw messages" + "enqueue one `inbox.process_message` per new
   message" (STEP 16/17), so a crash mid-sync no longer requires
   re-fetching from IMAP to retry the messages it already saw. Until
   this lands, `inbox.process_message` exists and is tested but nothing
   calls it in production - the sync loop still processes each message
   inline exactly as before batch 4.
