# Realtime Events (Phase 9)

## Status: load/inbox/communication/document event delivery foundation built; dispatch/driver events deferred

This is a **partial** Phase 9 pass, stacked on a **partial** Phase 8
(`feat/api-read-model`, PR #9, not yet merged). See "Phase 8 dependency"
below before relying on this doc as complete, and "What's NOT built this
pass" for genuine follow-ups.

## Why

Today, a change made by one dispatcher (a status transition, a new
Inbox item, an SMS delivery result) only becomes visible to another
connected client when that client's own Streamlit rerun happens to fire,
or a cache TTL (`services/tms_data_service.py::load_tms_data`'s 45s) or
manual page refresh happens to trigger a fresh read. There is no push
signal. Phase 9 adds one, without discarding the read model built in
Phase 8:

```text
Streamlit cache/rerun (today)          Phase 9 addition
------------------------------         -----------------------------
Client mutation                        Client mutation
    |                                       |
    v                                       v
Command commits to Postgres            Command commits to Postgres
    |                                       |  (same transaction)
    v                                       v
... nothing pushed ...                 domain_events row recorded
                                            |
    (next rerun / cache expiry)             v
    reads possibly-stale data          Realtime Publisher (async, separate
                                        process) broadcasts a small
                                        "here's what changed" message
                                            |
                                            v
                                        Connected clients invalidate/
                                        refetch the SAME Phase 8 API
                                        resource - the broadcast payload
                                        is never treated as the source of
                                        truth
```

## Architecture

```text
Application command (application/loads/commands.py, etc.)
     |
     v
db_client.transaction()
     |  business-state write
     |  realtime/events.py::publish_event(conn=conn, ...)  <- same transaction
     v
COMMIT  (both together, or neither - STEP 6)
     |
     v
services/realtime_publisher.py  (separate async process, same
     |                            scheduled-workflow cadence as the
     |                            outbox/worker processors)
     v
realtime/publisher.py::BroadcastPublisher
     |
     v
Supabase Realtime Broadcast REST endpoint (or NoOpBroadcastPublisher in
dev/test - see "Transport" below)
     |
     v
Connected client (future Next.js; Streamlit not wired to this yet - see
"Streamlit" below)
     |
     v
Client invalidates/refetches the Phase 8 API resource named in the
event's client-invalidation contract (STEP 17) - API stays authoritative
```

## Why not the outbox

Phase 6's `outbox_events` was considered and rejected as the storage
for realtime notifications, even though the schema shape is nearly
identical. The two have **incompatible failure-severity semantics**:

- `outbox_events`: loss is a business-correctness problem (an SMS never
  sent, a document never finalized). `mark_failed` is terminal and
  requires operator intervention (`repositories/outbox_repo.py::
  retry_event`) - the business process the event represents is
  considered *stuck* until someone looks at it.
- `domain_events` (this phase): loss is recoverable by design. A client
  that misses a broadcast, or receives it late, or never connects in
  time, simply refetches the same Phase 8 API resource on its next
  render/action. Nothing is "stuck" - the authoritative state was never
  in the event, only a pointer to it.

Reusing `outbox_events` for a `realtime.broadcast` event type would
force one of two bad outcomes: either realtime failures start paging an
operator the same way a failed SMS does (over-escalation, alert fatigue
for something recoverable), or the outbox's actual guarantee gets
quietly weakened for every event type sharing the table to accommodate
realtime's more relaxed tolerance. `worker_jobs` was never a serious
candidate either - it represents internal async work with no
"business transaction just committed, tell clients" semantics at all.

Result: a third table, `domain_events` (`database/
domain_events_migration.sql`), with the same proven claim/retry/backoff/
reclaim schema shape (`repositories/domain_event_repo.py` mirrors
`repositories/outbox_repo.py` almost exactly) but its own status
lifecycle (`pending -> processing -> published`, not `delivered`) and a
simpler, lower-stakes retry policy (`services/realtime_publisher.py`:
`MAX_ATTEMPTS = 5`, shorter backoff than the outbox's).

## Transport

**Supabase Realtime Broadcast**, called over plain HTTP via `requests`
(already a dependency - see `requirements.txt`) against Supabase's REST
Broadcast endpoint, rather than the `supabase-py` SDK (which pulls in
websockets/gotrue/postgrest/storage3 for one REST call this module
doesn't need - same "smallest production-appropriate mechanism"
reasoning Phase 7 used to choose GitHub Actions over a dedicated worker
process).

**Not verified against a live Supabase project** - no live Supabase
credentials are available in this development environment. The request
shape (`POST {SUPABASE_URL}/realtime/v1/api/broadcast`, `apikey` +
`Authorization: Bearer` headers, `{"messages": [{"topic", "event",
"payload"}]}` body) is implemented per Supabase's public documentation
as of this pass, not confirmed end-to-end against a real project. Treat
`realtime/publisher.py::SupabaseBroadcastPublisher` as implemented-but-
unverified until it is exercised against a real Supabase Realtime
channel.

**Dev/test default**: `NoOpBroadcastPublisher` - always "succeeds"
(the `domain_events` row is still recorded and marked `published`),
makes zero network calls. `realtime/publisher.py::get_publisher()`
returns this whenever `REALTIME_ENABLED` is unset or false. If
`REALTIME_ENABLED=true` but `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`
are missing, `get_publisher()` raises immediately instead of silently
falling back to the no-op (STEP 23) - a misconfigured "live" deployment
fails loudly, not quietly.

**Config** (`config.get_secret`, same env/.env/Streamlit-secrets
precedence every other module in this codebase uses - never
`st.secrets` directly): `REALTIME_ENABLED`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`. None of these are committed anywhere in
this repo.

**Wire payload (Phase 10 addition)**: `services/realtime_publisher.py::
_envelope_payload` wraps the domain event's `payload` (business
metadata - see "Security" below) inside a small envelope alongside its
bookkeeping fields, so a listening client has an ordering token and an
aggregate id even on a collection channel (which carries no id in its
topic name):

```json
{
  "event_id": 1042,
  "aggregate_type": "load",
  "aggregate_id": "381",
  "version": null,
  "occurred_at": "2026-08-15T18:04:02.113000+00:00",
  "metadata": {"new_status": "Dispatched", "old_status": "Verified"}
}
```

`event_id`/`aggregate_type`/`aggregate_id`/`version`/`occurred_at` come
from the `domain_events` row itself (never guessed or reconstructed by
the publisher); `metadata` is exactly what the emitting command passed
to `realtime/events.py::publish_event(metadata=...)`, still governed by
`realtime/channels.py::ALLOWED_METADATA_KEYS`. A client uses `event_id`
as the same-aggregate ordering token (STEP 23 of the Phase 10 spec):
track the highest `event_id` seen per `(aggregate_type, aggregate_id)`
and ignore a broadcast whose `event_id` is not greater than the last one
applied for that aggregate.

## Event durability

Every instrumented command records its domain event via `realtime/
events.py::publish_event(conn=conn, ...)` **inside the same
`db_client.transaction()` block** as the business-state write it
describes:

```python
with transaction() as conn:
    DispatchDatabaseClient().update_row_fields(load_id, updates, conn=conn, ...)
    publish_event(conn=conn, event_type="load.updated", ..., metadata={...})
```

If the business write fails, the transaction rolls back and the event
row never exists. If the event insert itself fails (e.g. a disallowed
metadata key - see "Security" below), the whole transaction rolls back,
including the business write - proven for representative commands by
`tests/test_domain_event_repo.py` (`test_business_write_failure_rolls_
back_the_event_enqueue_too`, `test_event_insert_failure_rolls_back_the_
business_write_too`) and by the SQLite-fixture atomicity tests added to
`tests/test_dispatch_transition_service.py`, `tests/
test_work_item_commands.py`, and `tests/
test_inbox_message_insert_idempotency.py`.

`publish_event` only *records* the event - it never calls Supabase
itself. Broadcasting happens later, asynchronously, via `services/
realtime_publisher.py` (see "Delivery semantics" below).

## Event catalog

Implemented this pass (`realtime/event_types.py`):

| Event type | Aggregate | Emitted from |
|---|---|---|
| `load.status_changed` | `load` | `services/dispatch_transition_service.py::apply_transition` |
| `load.assignment_changed` | `load` | `services/dispatch_transition_service.py::apply_transition` |
| `load.updated` | `load` | `application/loads/commands.py`'s direct-field-update commands (`mark_load_missing_info`, `save_load_note`, `verify_load_booking`, `cancel_load`, `update_load_fields`) |
| `inbox.received` | `order_intake_item` | `services/operations_inbox_service.py::_insert_operations_email_record_row` |
| `inbox.review_status_changed` | `order_intake_item` | `application/work_items/commands.py::close_work_item` |
| `communication.queued` | `dispatch_message` | `application/loads/commands.py::mark_load_ready_to_dispatch` |
| `communication.delivery_status_changed` | `dispatch_message` | `services/outbox_processor.py::_project_dispatch_message_status` |
| `document.available` / `document.failed` | `document` | `services/outbox_processor.py::_project_document_status` |

Not implemented this pass (documented in `realtime/event_types.py` and
below, not silently dropped):

- **`load.created`** - the underlying create path
  (`services.operations_inbox_service::create_load_from_inbox_item`) is
  itself multi-transaction, non-atomic (a documented Phase 1/2 known
  limitation - see `docs/architecture/BACKEND_BOUNDARY_PHASE_1.md`).
  Coupling an event to it "in the same transaction" per STEP 6 would not
  be honest until that path is itself made atomic - a bigger, separate
  piece of work.
- **`inbox.processing_completed`** - would need a single transaction
  boundary across a whole sync/process run, which Phase 7's async job
  model deliberately does not have (each message is its own
  independent job - see `docs/architecture/WORKER_RUNTIME.md`).

## Load events

`services/dispatch_transition_service.py::apply_transition` already runs
its assignment write, status write, and closeout write inside one
`db_client.transaction()` (the row is locked `FOR UPDATE` for the whole
call - see that function's docstring). Two new private helpers,
`_emit_load_status_changed_event` and `_emit_load_assignment_changed_event`,
are called at the end of that same block. Kept as two events, not one -
"driver/truck assignment is data, not a board stage" was already this
function's own design principle before Phase 9; a client that only cares
about assignment (e.g. a future driver-facing view) shouldn't have to
special-case a `status_changed` payload that happens to also carry
assignment fields.

The five direct-field-update commands in `application/loads/
commands.py` (`mark_load_missing_info`, `save_load_note`,
`verify_load_booking`, `cancel_load`, `update_load_fields`) previously
called `DispatchDatabaseClient().update_row_fields(...)` with no
explicit transaction - each internal statement (`read_df`, `execute`,
possibly the `status_events` audit insert) committed separately. Phase 9
introduced a shared `_update_load_fields_with_event` helper that wraps
these in `db_client.transaction()` so the `load.updated` event can be
recorded atomically with the write - a side effect, not the point of
this change, is that the write and its own status-change audit row are
now also atomic with each other, which they were not before.

`load.updated`'s metadata carries only the **names** of the changed
fields (`updated_fields: [...]`), never their values.

## Inbox events

`inbox.received` is emitted from `services/operations_inbox_service.py::
_insert_operations_email_record_row`, which already uses an idempotent
`ON CONFLICT (source_message_id) ... DO NOTHING RETURNING id` insert
(the Phase 7 closure fix for retried `inbox.process_message` jobs - see
`docs/architecture/WORKER_RUNTIME.md`'s "Genuine bug found and fixed"
section). That insert now runs inside an explicit `db_client.
transaction()` (previously its own bare `engine.begin()`), and the event
is only recorded when the insert actually returned a new id - a retried
insert that hits the `ON CONFLICT DO NOTHING` branch returns `None` and
records zero events, giving `inbox.received` idempotency for free
without a separate time-bucketed key.

`inbox.review_status_changed` is emitted from `application/work_items/
commands.py::close_work_item`, in the same transaction as its
`review_status` write and case-history audit row.

Not every Inbox state transition is instrumented - only the two above
(a new item became visible; an item closed). Finer-grained triage/owner
transitions are not wired up this pass (see "What's NOT built" below).

## Communications

`communication.queued` fires from `application/loads/commands.py::
mark_load_ready_to_dispatch`, in the same transaction as the existing
Phase 6 outbox enqueue - keyed on the fresh `dispatch_message_id` (no
time-bucketing needed; a real retry of the whole command is already
deduped by the outbox's own idempotency key upstream).

`communication.delivery_status_changed` fires from `services/
outbox_processor.py::_project_dispatch_message_status`, which already
runs inside the outbox processor's own per-result transaction. Keyed on
`(dispatch_message_id, attempt_count)` - not a flat id-only key - so
repeated `retrying` outcomes across multiple delivery attempts for the
same message each get their own event instead of colliding on `ON
CONFLICT DO NOTHING` after the first attempt.

Inbound SMS is out of scope (per the Phase 9 spec) - these events only
ever describe outbound delivery state.

## Documents

`document.available` / `document.failed` fire from `services/
outbox_processor.py::_project_document_status`, in the same transaction
as the `documents.status` write. Only the two terminal outcomes emit an
event - a mid-retry `pending` status is not pushed (STEP 8: "only
implement events where the distinction is meaningful"; a document
already showing "pending" in the UI doesn't need a push notification
that it's still pending).

## Security

Broadcast payloads are **invalidation-oriented, not a data channel**.
`realtime/channels.py::ALLOWED_METADATA_KEYS` is a strict **allowlist**
(not a denylist of "bad" words) - every metadata key any emitter uses
must be explicitly added there first. `realtime/events.py::publish_event`
calls `assert_no_sensitive_metadata` on every write, so an attempt to
include a disallowed field (a rate, an address, a phone number, a
message body) raises `ValueError` and the whole transaction rolls back
- caught at the moment a command tries to emit it, not discovered later
by an operator inspecting broadcast traffic.

No row-level authorization is enforced on Supabase Broadcast channels
this pass (no live Supabase project to configure Realtime Authorization/
RLS against). This is why payloads are deliberately minimal - even a
client subscribed to a channel it "shouldn't" see only learns that
*something* changed on *some* aggregate id, never the actual content.
Whether that client may act on the notification (refetch the real
resource) is still enforced by the Phase 5/5B `require_permission`/
FastAPI `require_role` checks on the API endpoint it refetches from -
the broadcast is never a substitute for that check, only a hint to make
a refetch call sooner.

## Client invalidation contract

| Event type | Client should invalidate/refetch |
|---|---|
| `load.status_changed`, `load.assignment_changed`, `load.updated` | `GET /api/v1/loads/search` (list), `GET /api/v1/loads/{id}/detail`, `GET /api/v1/loads/{id}/timeline` |
| `inbox.received`, `inbox.review_status_changed` | Whatever Inbox work-item list/detail endpoint the client is viewing (Phase 7's `application/work_items/queries.py`-backed API) |
| `communication.queued`, `communication.delivery_status_changed` | `GET /api/v1/loads/{id}/communications` |
| `document.available`, `document.failed` | `GET /api/v1/loads/{id}/documents` |

This is the bridge to a future TanStack Query-based client (Phase 10) -
not built this pass.

## Delivery semantics (stated precisely - no exactly-once claim)

1. **Database event persistence**: at-least-once relative to the
   business transaction. A `domain_events` row exists if and only if
   its accompanying business write committed (STEP 6, proven by the
   atomicity tests cited above).
2. **Publisher retry**: at-least-once from `services/
   realtime_publisher.py`'s side. A crash after a successful broadcast
   call but before `mark_published()` commits leaves the row
   `processing`; `reclaim_stuck_processing` (same 10-minute window as
   the outbox/worker processors) resets it to `pending`, and the next
   run broadcasts it again - a genuine duplicate broadcast in that
   narrow crash window, same accepted tradeoff as `services/
   outbox_processor.py`.
3. **Browser/broadcast delivery**: **not** exactly-once, and not even
   guaranteed at-least-once to a given browser tab. Supabase Realtime
   Broadcast over a websocket is fire-and-forget - a client that is
   disconnected, not yet subscribed, or drops the message for any
   reason simply never receives that particular notification. This is
   exactly why every entry in the client invalidation contract ends in
   an **authoritative API refetch**, never treats the broadcast payload
   itself as data. A missed notification costs a client "found out
   about the change on the next poll/rerender" instead of "found out
   immediately" - never a correctness bug.

## Phase 8 dependency

**Phase 9 is stacked on partial Phase 8 (PR #9, branch
`feat/api-read-model`, not yet merged).** This branch (`feat/realtime-
events`) was created from `feat/api-read-model`, not from `master`. The
client invalidation contract above references Phase 8's load-search/
detail/timeline/communications/documents endpoints because those are
the only Phase 8 read-model resources that exist yet.

## What's NOT built this pass (genuine follow-ups, not silently dropped)

- **Dispatch Board / Driver realtime events** - `GET /api/v1/dispatch/
  active` and `GET /api/v1/drivers` don't exist yet (still open Phase 8
  follow-ups per `docs/architecture/API_READ_MODEL.md`); there is
  nothing for a dispatch/driver event to invalidate until those APIs
  are built.
- **`load.created`, `inbox.processing_completed`** - see "Event
  catalog" above for why.
- **Next.js / TanStack Query client** - Phase 10, not started.
- **Streamlit realtime pilot** - no Streamlit page subscribes to
  Supabase Realtime this pass. Phase 9's scope is backend event
  delivery, not a Streamlit websocket retrofit (per the Phase 9 spec's
  own STEP 22). Streamlit continues to rely on its existing cache/
  rerun behavior, unchanged.
- **Supabase Realtime Authorization / RLS on broadcast channels** - not
  configured (no live Supabase project available this pass) - see
  "Security" above for how payload minimalism compensates in the
  meantime.
- **Live verification against a real Supabase project** - see
  "Transport" above.
- **Admin API event-health endpoint** (STEP 19, explicitly optional in
  the spec) - not built; `scripts/process_realtime_events.py status`
  (CLI) covers the same need per STEP 18's "prefer CLI" guidance.

## Manual verification (dev-only)

`scripts/watch_realtime_events.py` is a dev-only websocket subscriber
for manually confirming a Supabase Broadcast message sent by
`services/realtime_publisher.py` is actually received by a client -
useful because that publisher's transport is otherwise "implemented but
unverified" (see "Transport" above). It subscribes to exactly the
channel(s) `realtime/channels.py::channels_for` / `collection_channel`
would compute for a given aggregate type/id - the same functions the
publisher itself calls - so it cannot drift from the real channel
naming convention.

Exact command:

```
python scripts/watch_realtime_events.py load 381
```

(swap `load 381` for any `<aggregate_type> [aggregate_id]` pair, e.g.
`order_intake_item`, `dispatch_message`, `document`, with or without an
id - omitting the id subscribes to the collection channel only).

Requires `SUPABASE_URL` and `SUPABASE_ANON_KEY` (`config.get_secret`).
Never reads `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_SECRET_KEY` - a
client-side subscriber has no legitimate use for one. Pair it with a
separate `REALTIME_ENABLED=true` run of `scripts/
process_realtime_events.py process` (or `emit-test ... --i-know-this-
hits-a-real-channel`) against the same Supabase project to see a message
actually arrive.

Prints only `channel`, `event_type`, `aggregate_type`, `aggregate_id`,
`event_id`, `version`, `occurred_at` per received broadcast - never the
business `metadata` values. All of `event_id`/`aggregate_type`/
`aggregate_id`/`version`/`occurred_at` are read directly off the wire
envelope (see "Transport" above's "Wire payload" subsection) - none are
guessed from the topic name or print as `n/a` unless the field is
genuinely absent (a `NoOpBroadcastPublisher`-only environment producing
a hand-built test frame, for instance).

## Future clients

A future Next.js client (Phase 10) is the intended primary consumer:
subscribe to the `loads`/`inbox`/`communications`/`documents` collection
channels and the `load:{id}` resource channel for whatever load workspace
is currently open, and on any received event, invalidate the matching
TanStack Query key per the client invalidation contract above. No such
client exists yet - this document describes the contract it should
follow once built, not a working integration.
