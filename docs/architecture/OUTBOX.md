# Transactional Outbox (Phase 6)

## Why it exists

Some workflows combine a database write with an external side effect
(SMS, email, a future webhook/Motive call) that cannot be rolled back if
the database write fails, and cannot be made part of the same database
transaction because it's a network call to a third party. Before this
phase, `application/loads/commands.py::mark_load_ready_to_dispatch` sent
an SMS via Twilio *synchronously inside the command*, then wrote the
database - the two were never atomic with each other, and a crash or
failure between them could leave the load's assignment half-applied or
(worse) an SMS sent with no matching database state.

The outbox pattern separates **business transaction commit** from
**external side-effect execution**:

```
Application Command
        |
        v
Database Transaction
   +-- business state
   +-- outbox event
        |
      COMMIT
        |
        v
Outbox Processor (services/outbox_processor.py)
        |
        v
External Side Effect (e.g. Twilio SMS)
        |
        v
delivery status / retry state (outbox_events + dispatch_messages)
```

If the transaction rolls back, the business change rolls back, the
outbox record rolls back with it, and the external side effect is never
attempted - the two can no longer disagree about whether something
happened.

## Schema

`database/outbox_migration.sql` (registered in
`scripts/run_migrations.py::MIGRATION_ORDER`, verified by
`scripts/verify_schema.py`):

```
outbox_events
  id               bigserial primary key
  event_type       text        -- "driver_dispatch_sms" today
  aggregate_type   text        -- "load"
  aggregate_id     text        -- str(load_id)
  payload          jsonb       -- handler-specific, no secrets (see Security)
  idempotency_key  text unique -- see Idempotency
  status           text        -- pending | processing | delivered | failed
  attempt_count    integer
  available_at     timestamptz -- claimable once now() >= this
  created_at       timestamptz
  processed_at     timestamptz -- set on delivered
  last_error       text        -- sanitized, see Security
  actor            text        -- who triggered the originating command
```

Two indexes: `(status, available_at)` for the claim query, `(aggregate_type,
aggregate_id)` for "show me every outbox event for this load" lookups
(operator/debugging use, no code path depends on it yet).

## Transaction boundary

`repositories/outbox_repo.py::enqueue_outbox_event` takes a required
`conn` parameter and never opens or commits its own transaction - it
*must* be called with the same `db_client.transaction()` connection as
the business-state write it accompanies. This is not a convenience
default; there is no fallback that opens a separate transaction, because
that would defeat the entire point.

`mark_load_ready_to_dispatch`'s transaction, concretely:

```python
with transaction() as conn:
    DispatchDatabaseClient().update_row_fields(load_id, {...}, conn=conn, created_by=actor.actor)
    dispatch_message_id = _insert_dispatch_message(..., conn=conn, delivery_status="pending_delivery")
    enqueue_outbox_event(conn=conn, event_type="driver_dispatch_sms", ..., payload={..., "dispatch_message_id": dispatch_message_id})
```

All three statements commit together or roll back together. Proven by
`tests/test_outbox_repo.py::test_business_write_and_outbox_enqueue_commit_together`
and its two rollback-symmetry siblings (business-write failure rolls back
the enqueue; enqueue failure rolls back the business write).

## Processing lifecycle

Deliberately two separate short transactions per event, not one long
transaction spanning the network call (`repositories/outbox_repo.py`,
`services/outbox_processor.py::process_one`):

1. **Claim** (`claim_next_pending`): `SELECT ... FOR UPDATE SKIP LOCKED
   ... LIMIT 1`, then `UPDATE status = 'processing'`, committed
   immediately. `FOR UPDATE SKIP LOCKED` lets multiple worker processes
   poll the same table concurrently without blocking on or double-claiming
   the same row - each claims a different pending event. PostgreSQL-only;
   not supported by SQLite (see `tests/test_outbox_repo.py`'s module
   docstring for how that's handled in tests).
2. **External call**: the registered handler for the event's
   `event_type` runs with no open transaction (`services/
   outbox_processor.py::_EVENT_HANDLERS`).
3. **Result** (`mark_delivered` / `mark_retry` / `mark_failed`): a second
   short transaction records the outcome and projects it onto the
   accompanying `dispatch_messages.delivery_status` row, if the payload
   names one (`_project_delivery_status`).

## Crash windows and delivery guarantees

Two distinct crash windows, with different consequences - conflating
them would overstate what this design actually guarantees.

**Window A - crash after claim, before the external call.** A worker
that crashes between step 1's commit and starting step 2 leaves the
event `status = 'processing'` with nothing yet attempted against
Twilio. Left alone, nothing would ever pick this row up again (the claim
query only selects `'pending'` rows) - it would stay `'processing'`
forever. Fixed: `services/outbox_processor.py::process_pending` calls
`repositories/outbox_repo.py::reclaim_stuck_processing` automatically at
the start of *every* run (`RECLAIM_STALE_AFTER`, currently 10 minutes),
resetting any event that's been `'processing'` longer than that back to
`'pending'` - not an operator-remembered opt-in flag, though
`scripts/process_outbox.py process --reclaim-stuck-minutes N` still
exists to force a different threshold on demand. `reclaim_stuck_processing`
keys off `claimed_at` (set the instant a row transitions to `'processing'`),
not `created_at` - an earlier version of this had a real bug here: a row
created hours ago (having sat `'pending'` through earlier retries) but
claimed a second ago would be immediately eligible for reclaim under a
`created_at`-based check, even though it was only just claimed and might
still be genuinely in flight. `claimed_at` fixes that -
`tests/test_outbox_repo.py::TestPostgresOutboxLifecycle::
test_reclaim_uses_claimed_at_not_created_at` and
`test_reclaim_recovers_a_genuinely_stale_processing_event` prove both
halves. The 10-minute default is comfortably longer than any realistic
single handler call (Twilio's own HTTP timeout is 15s -
`services/driver_sms_service.py`), so a merely-slow-but-alive worker is
never falsely reclaimed while genuinely still working.

**Window B - crash after the external call succeeds, before the result
commits.** The hard case, and the one this design does **not** fully
solve. If a worker's Twilio call succeeds and the process then crashes
before `mark_delivered` commits, the event is still `'processing'`.
Window A's automatic reclaim - which exists specifically to recover
stuck events - will eventually reset it to `'pending'` and a future
worker will claim and reprocess it, calling the handler *again*: a
second, real SMS is sent for an event Twilio already delivered once.

**This system provides at-least-once delivery, not exactly-once, and
does not claim otherwise.** `services/driver_sms_service.py`'s Twilio
integration has no confirmed provider-side idempotency mechanism in this
codebase - not verified against current Twilio API capabilities in this
pass, so none is assumed or implemented; adopting one (if Twilio
supports a client-supplied idempotency key on this endpoint for this
account) is a documented future improvement, not something silently
claimed here. The practical mitigation implemented is minimizing Window
B's size: nothing runs between the handler call returning and the
`mark_delivered`/`mark_retry` transaction opening (`services/
outbox_processor.py::process_one`) - no logging, no extra I/O, just the
result transaction immediately after. That does not close the window,
it shrinks it to roughly one Python-to-Postgres round trip.

Three genuinely different guarantees are in play here, worth stating
explicitly since they're easy to conflate:

- **Enqueue idempotency** (`idempotency_key`, `ON CONFLICT DO NOTHING`):
  the same logical *command invocation*, resubmitted, does not create a
  second outbox row. See Idempotency below.
- **Worker claim concurrency** (`FOR UPDATE SKIP LOCKED`): two workers
  can never process the *same outbox row* at the same time. See Claiming
  below.
- **Provider delivery idempotency** (not implemented): two *different*
  outbox rows - or one row processed twice across a Window-B crash - can
  each independently and successfully call Twilio, and nothing in this
  design prevents that from being two real SMS deliveries. This is the
  at-least-once limitation.

## Retry policy

Bounded, exponential, capped (`services/outbox_processor.py`):

- `MAX_ATTEMPTS = 5` - on the 5th failure, the event moves to the
  terminal `'failed'` status instead of retrying again. An operator must
  look at `last_error` and decide (resend manually by re-enqueueing,
  investigate the provider, etc.) - the processor never retries a
  `'failed'` row on its own.
- Backoff: `30s * 2^attempt_count`, capped at `3600s` (1 hour) -
  30s, 60s, 120s, 240s, capped from there. Not a full scheduler; just
  enough to avoid hammering a provider that's already failing.

## Idempotency

`enqueue_outbox_event`'s `idempotency_key` is `unique`, inserted with
`ON CONFLICT (idempotency_key) DO NOTHING` - enqueueing the same logical
event twice is a no-op, not a duplicate row.

For `driver_dispatch_sms`, the key is built by
`application/loads/commands.py::_driver_dispatch_sms_idempotency_key` -
content-addressed **and** time-windowed, deliberately neither alone:

- **Pure content** (`load_id + phone + message`, no time component) was
  this key's first design in this phase, and it had a real bug: two
  genuinely separate Ready-to-Dispatch actions for the same load with
  byte-identical phone/message (e.g. the same driver re-dispatched days
  later with the same standard message text) would collide on the same
  key. `ON CONFLICT DO NOTHING` would then silently drop the second,
  legitimate SMS forever - no error, no event, nothing sent, the load
  still marked Ready to Dispatch as if it had gone out. That's not
  deduplication, that's silent message loss, and it was a defect, not an
  acceptable tradeoff.
- **Pure timestamp** (no content) defeats retry deduplication entirely -
  every resubmission, even a genuine same-click retry a second later,
  gets a fresh key and a duplicate send.

The fix: `_RETRY_DEDUPE_WINDOW_SECONDS` (300s) buckets time coarsely.
`key = sha256(f"{load_id}:{phone}:{message}:{floor(now/300)}")`. A
same-content resubmission within the same 5-minute bucket (a client
retry after a timeout, a double-click) maps to the same key and is
deduped; the same content sent again after the bucket rolls over (a
legitimate future re-dispatch) gets a new key and goes through.
Verified by `tests/test_load_commands_authorization.py::
test_mark_ready_to_dispatch_retry_within_window_is_idempotent` (same
bucket -> same key) and `test_mark_ready_to_dispatch_after_window_elapses_
is_a_new_event_not_suppressed` (different bucket -> different key, the
regression this fix addresses).

**Residual accepted risk**: two genuinely distinct dispatch actions with
byte-identical phone+message occurring within the same 5-minute window
would still collide and dedupe incorrectly. Judged acceptable for a
~10-20 driver fleet, where that specific coincidence (same driver, same
phone, byte-identical message text, within 5 minutes, for two different
logical dispatch events) is vanishingly unlikely - and strictly better
than either pure-content (permanent loss, no expiry) or pure-timestamp
(no dedup at all) alone.

This is **enqueue idempotency**: never insert a second outbox row for
what looks like the same logical command invocation. It is not
**provider delivery idempotency** - Twilio itself is not asked to
dedupe anything, so two *different* outbox rows (or one row reprocessed
after a Window-B crash - see Crash windows above) can each independently
succeed and produce two real SMS deliveries. And it is not **worker
claim concurrency** either - that's `FOR UPDATE SKIP LOCKED`, a separate
mechanism covered below. All three guarantees are real, distinct, and
none of them substitute for either of the others.

## Claiming / concurrency

Covered above (Processing lifecycle, step 1) and proven by
`tests/test_outbox_repo.py::TestPostgresOutboxLifecycle::
test_claim_does_not_return_an_already_processing_event` - gated behind
`MIGRATION_TEST_DATABASE_URL` (a disposable PostgreSQL database an
operator opts into), never the app's real `DATABASE_URL`, since `FOR
UPDATE SKIP LOCKED` has no SQLite equivalent.

## Operations

Run the processor:

```
python scripts/process_outbox.py process                              # up to 50 pending events, once
python scripts/process_outbox.py process --max-events 200
python scripts/process_outbox.py process --reclaim-stuck-minutes 15    # force a threshold other than
                                                                         # the automatic default (see
                                                                         # Crash windows above)
```

No daemon/scheduler is built in this phase (deliberately - "a simple
processor callable periodically is sufficient initially"). Run it via
cron, Windows Task Scheduler, or a future worker process, on whatever
cadence matches the business's tolerance for SMS delivery latency (every
1-5 minutes is reasonable for a ~10-20 driver operation).

**Operator recovery tooling** (`scripts/process_outbox.py`, backed by
`repositories/outbox_repo.py::list_by_status/get_event/retry_event/
retry_all_failed`) - every subcommand takes only integers/flags, never
free text that could shape a query, so there's no injection surface from
CLI arguments:

```
python scripts/process_outbox.py list-pending
python scripts/process_outbox.py list-failed
python scripts/process_outbox.py inspect 42                    # full detail, including payload
python scripts/process_outbox.py retry 42                      # requeue one failed event
python scripts/process_outbox.py retry 42 --reset-attempts      # ...and zero its attempt count
python scripts/process_outbox.py retry-all-failed --yes         # bulk requeue every failed event
```

`retry_event`/`retry_all_failed` only touch rows currently `'failed'` (a
`'pending'`/`'processing'` row is left alone - it's already going to be
picked up or is mid-flight), never insert a new row (same id, same
`idempotency_key` - identity preserved), and by default preserve
`attempt_count` (attempt history) rather than silently resetting it -
`--reset-attempts` is opt-in. `list-pending`/`list-failed` never print
`payload` (a quick operator scan, not for reading message content);
`inspect` does, for the one-event-at-a-time deep-dive case.
`retry-all-failed` requires `--yes` to proceed - the confirmation gate
runs before any database import, proven by
`tests/test_process_outbox_cli.py::
test_retry_all_failed_without_yes_is_a_no_op_and_touches_no_db`.

Migration/deployment order: `outbox_migration.sql` is independent (no
FKs), so it can run any time after `schema.sql`. Deploy the migration
before deploying code that calls `enqueue_outbox_event` (i.e., before the
Phase 6 application code goes live) - the reverse order would make
`mark_load_ready_to_dispatch` fail on its outbox insert against a
nonexistent table.

## Security

- **No secrets in payloads**: `driver_dispatch_sms`'s payload is `{to,
  message, dispatch_message_id}` - a phone number and message text, both
  already visible in `dispatch_messages` (an existing table with no
  stricter access control). No API keys, tokens, or credentials are ever
  placed in `outbox_events.payload`.
- **Sanitized errors**: every write to `outbox_events.last_error` goes
  through `utils/error_sanitizer.py`'s centralized sanitizer, at one
  choke point in `services/outbox_processor.py::process_one` - both a
  handler *exception*'s `str()` (`sanitize_exception_message`) and a
  handler's own *returned* failure string (`sanitize_message` - e.g. a
  provider's raw error response body, which is not an exception and
  would otherwise skip the exception path entirely) are sanitized before
  either reaches `mark_retry`/`mark_failed`. Proven by
  `tests/test_outbox_processor.py::
  test_process_one_handler_exception_is_sanitized_not_leaked` and
  `test_process_one_handler_returned_failure_string_is_also_sanitized`
  (fake DSN/API-key/bearer-token strings, never real secrets) - same
  pattern as every other error-sanitization boundary in this repo.
- **Actor/audit metadata**: `outbox_events.actor` records the
  `AuthenticatedActor.actor` identity that triggered the originating
  command (e.g. `mark_load_ready_to_dispatch`'s caller) - not used for
  re-authorization (see below), but available for audit/observability.
- **Authorization is unaffected**: the outbox processor runs as a
  service/worker identity and does **not** re-run human authorization.
  `require_permission` for `LOAD_READY_TO_DISPATCH` and
  `DRIVER_MESSAGE_SEND` still runs first, inside
  `mark_load_ready_to_dispatch`, before any read or write - an
  unauthorized actor never gets as far as enqueueing an event. The
  processor only ever sees already-authorized events; it has no
  permission model of its own to bypass.

## Ready-to-Dispatch: before/after

**Before (Phase 5)**: `send_sms()` called synchronously inside the
command; the database write (assignment + status + audit row) only
happened *after* Twilio reported success. A failed send meant zero
database mutation - but the SMS call and the database write were still
never atomic with each other (an SMS could succeed and the subsequent
database write could still fail, with no rollback of the SMS - it was
already sent).

**After (Phase 6)**: the database write (assignment + status +
`dispatch_messages` audit row + outbox event) is one atomic transaction
that commits immediately, regardless of whether the SMS is ever
delivered. `services/outbox_processor.py` sends the SMS afterward,
asynchronously, with retry. `ReadyToDispatchResult.sms_status` is
`"queued"`, and the Streamlit UI copy
(`pages_app/orders_management.py`) says "queued for delivery," not "text
sent" - no claim of delivery is made before delivery actually succeeds.

**Is SMS still sent inside the business transaction? No.**

This is a deliberate behavior change, not an oversight: a load can now
show "Ready to Dispatch" before its SMS is confirmed delivered.
`outbox_events.status` and `dispatch_messages.delivery_status` are the
source of truth for actual delivery state; "Ready to Dispatch" reflects
that the driver/truck/chassis assignment was made, which is real and
immediate regardless of SMS delivery timing. This is the explicit
tradeoff the outbox pattern makes (decoupling business-state commit from
external side-effect execution) - see `application/loads/commands.py::
mark_load_ready_to_dispatch`'s docstring for the same reasoning inline
with the code.

## Document attachments (Phase 6B, design only)

`application/documents/commands.py::attach_load_document` still writes
the uploaded file to local disk (`db_client.py::
DispatchDatabaseClient.attach_file_to_row`) *before* inserting the
`documents` row - a crash between those two steps leaves an orphaned
file with no matching database record. **Not converted in Phase 6** -
the task scope explicitly allowed producing a design instead of an
implementation here, and the two side effects have different enough
shapes that reusing the SMS outbox verbatim would be wrong:

- A file's bytes don't belong in `outbox_events.payload` (jsonb) -
  uploaded documents can be several MB; storing that in a queue table
  bloats it and makes the outbox itself a backup/replication burden it
  was never meant to carry.
- Unlike SMS (fire-and-check-later), the file write is *local and fast*
  - it doesn't need asynchronous delivery, it needs the two writes (file,
  DB row) to become one atomic unit, or to be made idempotent/repairable
  if they aren't.

Three strategies considered for a future Phase 6B, in order of
preference for this codebase's scale (a single Streamlit app writing to
local disk, not object storage):

**A. Write to a temp path, finalize after the DB transaction commits.**
Write the upload to a temp file first; insert the `documents` row inside
a transaction referencing the *final* deterministic path
(`load_{id}_{filename}`, already how `attach_file_to_row` names files);
only rename the temp file to its final path *after* the transaction
commits. If the process crashes before rename, the temp file is orphaned
but the DB has no row pointing at the final path - no "phantom" document
appears to users. A rename is atomic on the same filesystem/volume,
which this app satisfies (`DOCUMENT_STORAGE_DIR` is a single local
directory). Cleanup of stray temp files becomes a periodic sweep (temp
files older than N hours with no matching `documents` row), analogous to
`reclaim_stuck_processing` above. **Recommended** - smallest change,
matches this app's actual storage model (local disk, not S3/object
storage), and doesn't require the outbox table at all.

**B. Object-storage-first with a durable idempotent key.** Upload to
object storage (S3-compatible) first, keyed by a deterministic path; only
then insert the DB row referencing that key. Correct and how most
production systems solve this, but requires provisioning object storage
this codebase does not currently have (`DOCUMENT_STORAGE_DIR` is local
disk) - out of scope for a documents-attachment fix; would be justified
if/when this app moves off Streamlit Cloud's local filesystem, not before.

**C. A durable file-operation outbox** (reusing this phase's
`outbox_events` table with `payload` holding a *reference* - the
deterministic path - never the file bytes themselves). Closest in shape
to the SMS conversion, but adds an async hop (a processor writing the
file) to what's currently a synchronous, fast, local operation with no
real reason to be asynchronous - SMS needed the outbox because Twilio is
slow/unreliable/external; local disk writes aren't. Not recommended
unless this app's storage genuinely becomes external/slow later (at
which point B becomes the better answer anyway).

**Decision for Phase 6B (when undertaken): strategy A.** No code changes
were made in Phase 6 to implement this - `attach_load_document` and
`DispatchDatabaseClient.attach_file_to_row` are unchanged, and the
existing orphan-file risk remains, now with this design on record for
whoever picks up Phase 6B.

## Future integrations

New external side effects register a handler in
`services/outbox_processor.py::_EVENT_HANDLERS` and pick an `event_type`
following a `<domain>.<entity>.<action>` naming convention, e.g.:

- `motive.load.sync`
- `motive.driver.assignment`
- `motive.vehicle.location_subscription`
- `email.customer_status.send` (would replace
  `services/customer_status_email_service.py`'s direct SMTP calls, not
  audited/converted in this phase - a candidate for a later pass, called
  out here so it isn't lost)
- `webhook.<name>.deliver`

These are conceptual only - **no Motive integration, email conversion, or
webhook delivery was implemented in Phase 6.** The point of naming the
convention here is so the next integration that needs "commit business
state now, deliver an external side effect reliably afterward" reuses
this infrastructure instead of inventing a fourth non-atomic pattern.
