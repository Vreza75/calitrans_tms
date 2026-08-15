# Worker Runtime (Phase 7)

## Status: closure pass complete

Phase 7 (Worker Runtime + Operations Inbox Processing Extraction) was
built across 5 reviewed batches plus a closure pass. This document
reflects final scope, not a batch-in-progress snapshot (see git history
on `feat/worker-runtime-inbox` / PR #8 for how it got here incrementally).

> **Streamlit is now a transitional client. It no longer owns the live
> Operations Inbox processing pipeline.** The "Sync Email Engine" button
> enqueues a job and returns; the actual fetch/parse/classify/match/
> persist work happens in `workers/`, runnable with zero Streamlit
> dependency.

## Runtime processes

```text
calitrans-api                  FastAPI (api/main.py, uvicorn)
calitrans-worker               scripts/process_worker_jobs.py + scripts/process_outbox.py
                                + scripts/process_realtime_events.py (Phase 9)
legacy-calitrans-streamlit     Streamlit (app.py) - transitional client
```

All three share the same `application/`, `services/`, `repositories/`,
and PostgreSQL database - one Python backend, not microservices. Nothing
in `workers/`, `repositories/worker_job_repo.py`, or
`application/inbox/` imports Streamlit (enforced by
`tests/test_backend_boundary_architecture.py`).

## Operational run mode

**How the worker actually runs in production**: `.github/workflows/
process-jobs.yml`, a GitHub Actions scheduled workflow (`cron: */5 * * *
*`, plus `workflow_dispatch` for manual runs) that executes both `python
scripts/process_worker_jobs.py process` and `python
scripts/process_outbox.py process` every 5 minutes.

This is the smallest production-appropriate mechanism given this repo's
actual deployment evidence: no Dockerfile, no Procfile, no render/fly/
railway config - only a Heroku-style `runtime.txt` (`python-3.11`) with
no matching Procfile to confirm an active worker dyno. Nothing
demonstrates a long-running second process is currently hostable, so
this uses **scheduled invocation**, not a dedicated always-on worker
loop. If the actual deployment target is later confirmed to support a
persistent process, a `calitrans-worker` loop (claim → execute →
sleep, repeating) becomes viable and this file should be updated - not
assumed now.

**Documented limitation**: GitHub Actions scheduled workflows are
best-effort, not guaranteed-on-time (GitHub's own docs: delays are
possible during high load; a schedule is disabled automatically after 60
days of repository inactivity). This is a real constraint of the
mechanism, not a bug in this implementation.

**Required repository secrets** for the workflow to function:
`DATABASE_URL` (both steps), `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/
`TWILIO_FROM_NUMBER` (outbox step), `IMAP_SERVER`/`SMTP_HOST`/
`DISPATCH_YAHOO_EMAIL`/`DISPATCH_YAHOO_APP_PASSWORD`/`MARGIE_YAHOO_EMAIL`/
`MARGIE_YAHOO_APP_PASSWORD`/`ACCOUNTING_YAHOO_EMAIL`/
`ACCOUNTING_YAHOO_APP_PASSWORD` (worker-jobs step, for `inbox.sync`'s
mailbox fetch). None of these are committed anywhere in this repo.

**Concurrency**: overlapping runs are already safe, not just tolerated -
`claim_next_job`/`claim_next_pending`'s `FOR UPDATE SKIP LOCKED` means
two concurrent invocations claim different rows, never the same one
(proven by PostgreSQL-gated tests). The workflow's `concurrency:` group
exists only to avoid wasting Actions minutes on a genuinely overlapping
run, not to prevent a correctness problem.

## Worker loop (per invocation)

```text
reclaim_stuck_processing (claimed_at older than RECLAIM_STALE_AFTER)
        |
        v
claim (short transaction, commits immediately)
        |
        v
execute handler (no open transaction - email fetch, parsing, attachment
                  I/O, AI calls all happen here with zero DB lock held)
        |
        v
result (short transaction: mark_completed / mark_retry / mark_failed)
```

- **Claim strategy**: `FOR UPDATE SKIP LOCKED`, PostgreSQL-only (not
  SQLite-testable - see `tests/test_worker_job_repo.py`'s
  `TestPostgresWorkerJobLifecycle`, gated behind
  `MIGRATION_TEST_DATABASE_URL`, never the real `DATABASE_URL`).
- **Retry**: bounded, `MAX_ATTEMPTS = 5` (5 total attempts: initial +
  4 retries), exponential backoff (30s/60s/120s/240s/480s, capped at
  3600s). Terminal `'failed'` state requires operator action
  (`scripts/process_worker_jobs.py retry`) - never auto-retried past the
  cap.
- **Stale recovery**: `claimed_at`-based (not `created_at` - a row can
  sit `'pending'` a long time across earlier retries before a given
  claim, so `created_at` is already stale by the time that claim needs
  bounding). `RECLAIM_STALE_AFTER = 15 minutes` (more conservative than
  outbox's 10 - inbox processing may involve AI/attachment work with
  less predictable latency than Twilio's fixed 15s timeout; a documented
  placeholder, not measured production data).
- **Error sanitization**: every write to `worker_jobs.last_error` passes
  through `utils.error_sanitizer` at one choke point (`workers/
  processor.py::process_one`), for both a handler's raised exception and
  its own returned failure string - proven with fake-credential
  regression tests (`tests/test_worker_processor.py`).

## Worker jobs vs. outbox events

Two distinct concepts, kept in two separate tables/modules - conflating
them was an explicit anti-goal for this phase:

| | `outbox_events` (Phase 6) | `worker_jobs` (Phase 7) |
|---|---|---|
| Represents | A business transaction committed; an external side effect must happen | Internal asynchronous work must be performed |
| Examples | `driver_dispatch_sms`, `document.file.finalize` | `inbox.sync`, `inbox.process_message` |
| Triggered by | A business command's own transaction, alongside the state it accompanies | A user request or another job, not necessarily paired with a business-state write |
| External provider involved | Usually (Twilio, filesystem) | Not necessarily |

Same schema shape (`pending -> processing -> completed/delivered`,
`claimed_at`-based stale reclaim, bounded retry with backoff,
`idempotency_key` unique constraint) because the reliability problem is
identical - proven in Phase 6, reused here rather than re-invented. Kept
as separate tables/repositories/CLIs on purpose - the GitHub Actions
workflow runs both, independently, in one scheduled invocation (see
"Operational run mode"), with `continue-on-error` so one subsystem's
failure never blocks the other's step from running.

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

## Inbox flow

```text
Streamlit button / POST /api/v1/inbox/sync
        |
        v   request_inbox_sync (application/inbox/commands.py)
        |   Permission.WORK_ITEM_MANAGE checked first
        v
enqueue inbox.sync  ---------------------------- returns immediately
        |
        v   [worker, later]
handle_inbox_sync -> _fetch_and_enqueue_inbox_messages
        |   fetch (email_client.fetch_operations_email_sync)
        |   dedupe (operations_email_already_imported)
        |   save attachments to disk NOW (raw bytes never enter a payload)
        v
enqueue inbox.process_message (one per new message)
        |
        v   [worker, later - may be the same invocation, may not be]
handle_inbox_process_message
        |   _insert_operations_email_message (unchanged pipeline:
        |     latest-body extraction, CASE-010 segmentation, parse,
        |     attachment field merge, classification/triage, load
        |     matching, persist)
        |   sync_conversation_status (best-effort, per message)
        v
Inbox work item (order_intake row), reviewable via
GET /api/v1/work-items / the Operations Inbox page
```

## Idempotency

- **`inbox.sync`**: `inbox.sync:<source>:<60s time bucket>`
  (`application/inbox/commands.py::_inbox_sync_idempotency_key`). Unlike
  the SMS dedupe key, this window exists only to collapse an accidental
  double-click into one job - syncing the same mailbox twice is
  harmless (per-message dedup happens downstream), so a request 61
  seconds later correctly gets a fresh job, not suppression.
  `_SYNC_SOURCE = "primary_mailbox"` is a constant (this system syncs
  exactly one configured mailbox today; becomes a parameter if that
  changes - not built now, YAGNI).
- **Source-message dedupe**: `operations_email_already_imported` (checked
  during fetch, against `order_intake` + an in-memory lookup built at
  the start of the fetch loop) - the same function
  `sync_operations_email_engine` always used, unchanged.
- **`inbox.process_message`**: `inbox.process_message:<provider-message-id>`
  (`_email_sync_unique_message_id`). One message, one job, forever - same
  reasoning as Phase 6B's `document_finalize:{document_id}` key, not the
  SMS-style time-bucketed key, since a provider message id is a stable,
  pre-existing unique identity. This also protects against the case the
  fetch-time dedupe check can't: two `inbox.sync` runs both fetching the
  same not-yet-imported message (nothing in `order_intake` yet to detect)
  still resolve to one `worker_jobs` row, not two (`tests/
  test_inbox_handlers.py::test_duplicate_inbox_sync_does_not_duplicate_
  process_message_jobs`, against a real SQLite DB).
- **Attachment dedupe**: content-hash-based within one message
  (`_save_operations_email_attachments`' `seen_hashes` set, unchanged) -
  not new to Phase 7.
- **`order_intake` insert retry safety** (closure-pass fix): a reclaimed
  `inbox.process_message` job retrying after a crash between its INSERT
  committing and the job's own completion write committing used to hit
  `order_intake`'s existing unique index on `source_message_id` and
  raise - a real bug this phase introduced by adding retry on top of an
  insert that was never designed to be retried. Fixed with `ON CONFLICT
  (source_message_id) WHERE source_message_id IS NOT NULL DO NOTHING
  RETURNING id` in `_insert_operations_email_record_row` (same pattern
  as `db_client.py::add_row`'s `ux_loads_source_intake_id` handling) -
  proven safe against a real SQLite DB with the matching partial unique
  index (`tests/test_inbox_message_insert_idempotency.py`).

`enqueue_job` returns the job's `id` whether freshly inserted or already
existing for that `idempotency_key` (`ON CONFLICT ... DO UPDATE ...
RETURNING id`, the "update" being a no-op self-assignment used only to
make `RETURNING` fire on conflict too).

## Health / operational status

Minimal, per Phase 7 STEP 5 - not a monitoring platform:

- `GET /api/v1/worker/status` (`application/jobs/queries.py::
  get_worker_runtime_status`) - queue depth per status for both
  `worker_jobs` and `outbox_events`. **Reflects database state only** -
  it cannot and does not claim the external scheduled runner is
  currently alive. A growing `'pending'` count with no recent
  `'completed'`/`'delivered'` activity is the actual signal an operator
  should watch for that, not a boolean "healthy" flag this endpoint
  deliberately does not provide.
- `GET /api/v1/jobs/{job_id}` (`application/jobs/queries.py::
  get_worker_job_status`) - single job detail, safe fields only (never
  the raw `payload`; `last_error` is already sanitized at write time, no
  further processing needed at read time).
- `scripts/process_worker_jobs.py list-pending` / `list-failed` /
  `inspect <id>` - CLI equivalent, same underlying repository functions.
- GitHub Actions' own run history (Actions tab) is a free, real "last run
  succeeded/failed and when" signal - not rebuilt here.

## API endpoints (Phase 7 additions)

- `POST /api/v1/inbox/sync` - enqueue-only, `202 Accepted`. Authorizes
  (`Permission.WORK_ITEM_MANAGE`, same as the Streamlit button), enqueues
  `inbox.sync`, returns `{ok, job_id, status, reason}`. Does not fetch or
  parse email inline, does not wait for spawned `inbox.process_message`
  jobs.
- `GET /api/v1/jobs/{job_id}` - safe job status (see "Health" above).
- `GET /api/v1/worker/status` - queue depths (see "Health" above).

**Not built** (found already covered, not duplicated - see "Scope
corrections" below): `GET /api/v1/inbox` and `GET /api/v1/inbox/{id}`.
`GET /api/v1/work-items` and `GET /api/v1/work-items/{id}`
(`api/routers/work_items.py`, built in an earlier phase) already deliver
this - paginated/filtered list, detail, `/attachments`, `/conversation`,
`/draft` sub-resources.

## Live UI path: before/after

**Before Phase 7**: Streamlit button → `sync_operations_email_engine`
(inline, up to 25s blocking) → fetch, parse, CASE-010 segment, classify,
match, save attachments, persist, batch-reconcile conversation status →
render result from `st.session_state`.

**After Phase 7**: Streamlit button → `request_inbox_sync` → enqueue
`inbox.sync` → **return immediately** (`st.success("Inbox sync
queued...")`, `st.rerun()`) → \[worker, out of the request\] fetch,
dedupe, save attachments, enqueue `inbox.process_message` per message →
\[worker, out of the request\] parse, classify, match, persist, per
message.

Proven by source inspection (`tests/
test_operations_inbox_sync_button_async.py`): the button's handler
source contains `request_inbox_sync(actor=principal)` and does **not**
contain `ops.sync_operations_email_engine(`.

`sync_operations_email_engine` itself is byte-for-byte unchanged and
still used directly by `pages_app/email_imports.py`'s admin debug "Sync
Recent Mail Then Refresh This Thread" tool - kept for
compatibility/certification and because immediate feedback is the point
of that specific debugging tool, per its own docstring/comments.
Deliberately not converted.

## Business-state ownership

No business state depends solely on `st.session_state` after Phase 7:
review status, processing status, queue, owner, triage/classification,
matched load, retry/failure state, action required all live in
`order_intake`/`worker_jobs`/`outbox_events`, queryable independent of
any Streamlit session. `operations_email_sync_running` (a UI busy-flag,
not business state) was removed entirely from the button - the action
is now a fast enqueue, not a 25s blocking call, so the flag had nothing
left to guard; the job's own idempotency key already collapses
accidental double-clicks. `operations_email_import_result` (session-only
sync-result display) remains, narrowed to `{queued, job_id}` for the
main button; the admin debug page's own thread-sync button still
populates it with the full result dict from `sync_operations_email_engine`
directly - both write the same session key, so the debug page's
diagnostic JSON display shows different shapes depending on which button
ran last. Cosmetic only (`st.json()` renders either fine), not fixed -
`pages_app/email_imports.py` is explicitly out of this phase's UI scope.

## System actor

No `Role.SYSTEM`/`ActorType` enum added. `repositories/work_item_repo.py
::insert_case_event`'s `actor: str = "dispatcher"` parameter (and the
equivalent pattern elsewhere) already takes a plain string - every
caller passes it explicitly. Worker-originated writes pass
`SYSTEM_ACTOR_INBOX_WORKER = "system:inbox-worker"`
(`workers/inbox_handlers.py`) instead of a human actor's email - used on
every `inbox.process_message` job's `actor` column. No schema or
auth-model change was needed. User-triggered commands
(`request_inbox_sync`) remain fully permission-gated; only the
worker-internal continuation of already-authorized work uses the system
identity.

## Scope corrections from the original Phase 7 brief

1. **No duplicate Inbox query/detail layer.** The original brief assumed
   a green-field `application/inbox/` covering models, commands, *and*
   queries (`InboxWorkItemSummary`, `InboxWorkItemDetail`,
   `InboxQueueFilter`, `GET /api/v1/inbox` + `/{id}`). That already
   existed under `application/work_items/` + `api/routers/work_items.py`
   from an earlier phase. `application/inbox/` owns only what's
   genuinely new: triggering (`request_inbox_sync`) and processing
   (`workers/inbox_handlers.py`) the sync pipeline. Verified by identity
   check, not just convention: `tests/
   test_backend_boundary_architecture.py::
   test_streamlit_and_fastapi_share_the_same_inbox_sync_command`.
2. **Job-status/health models live in `application/jobs/`, not
   `application/inbox/`.** Worker jobs aren't Inbox-specific (future job
   types - `motive.event.process`, `communications.delivery.process` -
   aren't inbox concepts either), so `WorkerJobStatus`/
   `WorkerRuntimeStatus` and their queries got their own package rather
   than being crammed into the Inbox one.

## Genuine bug found and fixed during the closure review

Independent re-review (tracing code, not trusting prior batch summaries)
found that Phase 7's retry/reclaim layer introduced a real crash-window
risk that never existed before it: a reclaimed `inbox.process_message`
job retrying its `order_intake` INSERT after the first attempt already
committed would raise a raw unique-violation and terminal-fail, even
though the message was already correctly persisted. See "Idempotency"
above for the fix. Full Operations Inbox test suite (157+ tests) and the
full repository suite were re-run clean after the fix, plus two new
dedicated regression tests.

## Future phases (not started here)

- **Phase 8** - API Read Model + Pagination/Search/Filtering (partial -
  see `docs/architecture/API_READ_MODEL.md`).
- **Phase 9** - realtime event delivery - see `docs/architecture/
  REALTIME_EVENTS.md`. Its publisher (`scripts/process_realtime_events.py`)
  runs in the same `.github/workflows/process-jobs.yml` scheduled
  workflow as this phase's two processors, as a third independent step.
- **Phase 10** - dedicated web client (Next.js).
- Later: Motive integration.
