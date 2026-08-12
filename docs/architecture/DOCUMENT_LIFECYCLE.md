# Reliable Document / File Lifecycle (Phase 6B)

## The problem

`application/documents/commands.py::attach_load_document` used to call
`db_client.py::DispatchDatabaseClient.attach_file_to_row`, which wrote
the uploaded file to local disk **before** inserting the `documents`
row:

```
write file to disk
        |
        v
insert documents row
```

A crash between those two steps left an orphaned file with no DB record
pointing to it. There was also a second, less obvious defect: the
storage key was `load_{id}_{original_filename}` - two uploads to the
same load with the same original filename (e.g. two files both named
`invoice.pdf`) silently overwrote each other's bytes on disk, even
though each got its own `documents` row.

## Storage architecture found

**Local disk, one directory, single filesystem.** `config.py::
DOCUMENT_STORAGE_DIR` (default `storage/load_documents`, overridable via
the `DOCUMENT_STORAGE_DIR` secret) is a plain filesystem path -
`db_client.py`'s pre-existing `attach_file_to_row` just does
`Path(DOCUMENT_STORAGE_DIR).mkdir(...)` and `file_path.write_bytes(...)`.
No object-storage client, no S3/GCS/Azure Blob SDK, no `boto3` or
equivalent dependency anywhere in `requirements.txt`. `storage/` is in
`.gitignore` (per `CLAUDE.md`'s never-commit list), confirming uploaded
files were never intended to be part of the repository.

**Durability: not verified, and not assumed.** This repository has no
Dockerfile, no persistent-volume configuration, and no evidence of a
mounted/shared storage layer - just `.streamlit/secrets.toml`.
`CLAUDE.md` names "Streamlit Cloud deployment" as a thing to preserve.
Streamlit Community Cloud's own documented behavior is that an app's
local filesystem is **not** guaranteed to survive a reboot/redeploy/
resource-recycle event (only Streamlit's own persistent mechanisms -
`st.cache_data`/`st.session_state`/external storage - survive that; raw
local files written by app code do not). Whether *this* deployment is
Streamlit Community Cloud specifically, a self-hosted always-on server
(where local disk genuinely would be durable), or something else could
not be confirmed from the repository alone - **this is stated as
uncertain, not guessed as either answer.**

**Multiple instances**: no evidence any load-balancing/multi-instance
setup exists (no session-affinity config, no shared-storage config) -
assumed single-instance for this phase, consistent with the business's
scale (~10-20 drivers, one dispatcher).

**Consequence for this phase's scope**: given that uncertainty, Phase 6B
implements the fix for the **atomicity/consistency problem** (orphaned
files, inconsistent status, silent filename collisions) that is real and
worth fixing regardless of the underlying storage's durability. It does
**not** claim to solve platform-level ephemerality - if the deployment
target turns out to be Streamlit Community Cloud without a mounted
persistent volume, uploaded documents can still be lost entirely on a
redeploy, atomicity fix or not. That is a **separate, larger problem**
(migrating to object storage - Strategy B below) that this phase
explicitly does not attempt, and that should be resolved - by confirming
the actual deployment infrastructure and migrating to object storage if
it is confirmed ephemeral - before this system is relied on for
documents that must never be lost (e.g. signed PODs, rate confirmations
needed for billing disputes).

## Chosen design: staged write + outbox-driven atomic finalize

```
BEGIN (no DB transaction yet)
  stage upload to a temp path, checksum it
DB TRANSACTION
  insert documents row (status='pending', checksum, file_path=final key)
  enqueue 'document.file.finalize' outbox event
COMMIT

outbox processor (services/outbox_processor.py - same infrastructure
Phase 6 built for SMS)
  claim event
  atomically rename staged file -> final path
  update documents.status = 'available'
```

This reuses the generic outbox infrastructure `services/
outbox_processor.py` already provides (claim/retry/backoff/reclaim),
rather than building a second parallel reliability mechanism - a new
event type (`document.file.finalize`) and a new handler
(`services/document_storage_service.py::finalize`) were the entire
integration point.

### Why not the other two strategies (from the Phase 6 design notes)

- **B. Object-storage-first.** The theoretically correct answer once
  storage durability is confirmed to matter - not implemented because
  this codebase has no object-storage client/credentials/dependency
  today, and provisioning one is a real infrastructure decision outside
  this phase's scope (see "Storage architecture found" above).
- **C. A durable file-operation outbox carrying file bytes in the
  payload.** Rejected - `outbox_events.payload` is `jsonb`; a multi-MB
  PDF does not belong there (bloats the table, becomes a
  backup/replication burden the outbox was never meant to carry). The
  chosen design keeps bytes on the filesystem throughout and only puts
  *references* (paths, a checksum) in the outbox payload.

## Schema

`database/document_lifecycle_migration.sql` (registered in
`scripts/run_migrations.py::MIGRATION_ORDER`, auto-verified by
`scripts/verify_schema.py`):

```sql
alter table documents add column if not exists status text not null default 'available';
alter table documents add column if not exists checksum text;
create index if not exists idx_documents_status on documents(status);
```

`status` defaults to `'available'` for the ALTER itself (every
pre-existing row already has its file on disk with no pending/failed
concept - that default is correct for those rows, not a guess). Every
**new** row inserted via `attach_load_document` explicitly starts at
`'pending'` (`repositories/document_repo.py::insert_pending_document`) -
the column default only matters for rows that predate this migration.

No `staged_path` column was added - the staging location is
handler-specific metadata that only matters for the lifetime of one
outbox event, so it lives in `outbox_events.payload`
(`{document_id, staging_path, final_storage_key, checksum}`), not as a
permanent column on `documents`.

## Document states

```
pending -> available   (finalize succeeded)
pending -> pending      (finalize failed, retry scheduled - status
                          stays 'pending', not a separate 'retrying'
                          state; outbox_events tracks the retry/attempt
                          bookkeeping, documents.status only needs to
                          distinguish "not yet usable" from "usable" from
                          "gave up")
pending -> failed        (finalize failed MAX_ATTEMPTS times - terminal,
                          same policy as the SMS outbox event)
```

The database never claims `'available'` before the file is actually at
its final path - `repositories/document_repo.py::update_document_status`
is only called with `'available'` from the outbox processor's success
path (`services/outbox_processor.py::_project_document_status`), never
from `attach_load_document` itself.

## Identity, idempotency, and collision safety

**Storage key**: `load_{load_id}_{uuid4().hex}_{sanitized_original_filename}`
(`services/document_storage_service.py::stage_upload`) - never the
original filename alone. This fixes the pre-existing silent-overwrite
bug: two uploads with the same original filename to the same load now
get distinct storage keys (`tests/test_document_lifecycle.py::
test_stage_upload_two_uploads_same_original_filename_do_not_collide`).
The original filename is preserved as `documents.filename` (metadata,
for display) but never used to build a filesystem path on its own.

**Outbox idempotency key**: `f"document_finalize:{document_id}"` -
simpler than the SMS event's content+time-window key, deliberately.
Unlike an SMS (where the same content might legitimately be sent again
days later), a `documents` row is inherently a single, unique logical
event - a fresh attach always produces a fresh row via a fresh command
call, so there is no "is this a retry or a legitimate new request with
identical content" ambiguity to resolve. One document row, one finalize
event, forever.

**Finalize idempotency** (`services/document_storage_service.py::
finalize`): if the final file already exists with a matching checksum,
finalize reports success without touching anything - a retry after a
crash between the rename and the outbox result commit (crash window 3
below) must not fail just because the staged file is already gone (it
was already moved) or re-move/corrupt anything. If a final file exists
with a **different** checksum, finalize refuses and reports an error
rather than overwriting - astronomically unlikely given the uuid4
token, but refuse-don't-overwrite is the safe default either way.

## Security

- **Path traversal**: `services/document_storage_service.py::
  _sanitize_original_filename` reduces the user-supplied filename to
  `Path(...).name` (strips any leading directory component recognized by
  either POSIX or Windows separator conventions - `"../../secret.txt"`,
  `"folder/file.pdf"` both reduce to their final segment) and explicitly
  rejects the residual edge cases `.name` alone doesn't fully normalize:
  a bare `"."`/`".."`, or any name still containing `/` or `\` after
  that reduction (POSIX does not treat backslash as a separator, so a
  Windows-style traversal string sent to a POSIX-hosted app would
  otherwise pass through unchanged as an odd-but-inert filename - this
  rejects it outright rather than relying on that inertness). Even
  without this extra check, the mandatory `load_{id}_{uuid}_` prefix on
  every generated storage key means the final key can never literally
  equal `"."` or `".."` - the checks in `_sanitize_original_filename` are
  defense in depth on top of that, not the only thing preventing escape.
  Tested with the exact traversal strings from the closure task:
  `"../../secret.txt"`, `"..\\..\\secret.txt"`, `"folder/file.pdf"`,
  bare `".."`, `"."`, and an empty name
  (`tests/test_document_lifecycle.py::
  test_stage_upload_sanitizes_malicious_filenames_and_stays_inside_storage_root`,
  which asserts the resolved staged/final paths are provably inside
  `DOCUMENT_STORAGE_DIR` via `Path.is_relative_to`, not just a substring
  check).
- **Authorization before staging**: `attach_load_document` calls
  `require_permission(actor, Permission.DOCUMENT_ATTACH)` as its first
  action, before `stage_upload` (which touches the filesystem) is even
  called. An unauthorized actor produces zero staged file, zero
  `documents` row, zero outbox event - proven by
  `tests/test_document_lifecycle.py::
  test_unauthorized_actor_creates_zero_staged_file_zero_row_zero_event`.
- **No raw filesystem paths in the UI**: `pages_app/documents.py` and
  `pages_app/dispatch_board.py`'s document listings no longer select/
  display `documents.file_path` - only metadata (filename, type, source,
  status, timestamps). `file_path` itself changed meaning in this phase
  too (a storage key relative to `DOCUMENT_STORAGE_DIR`, not an absolute
  server path), which independently lowers what a leak would even
  disclose, but the display was tightened regardless.

## Failure/recovery, by crash window

1. **Staged file written, process dies before the DB transaction
   commits.** No `documents` row, no outbox event exist - the staged file
   is genuinely orphaned with nothing referencing it.
   `services/document_storage_service.py::reclaim_orphaned_staging_files`
   sweeps `.staging/` for files older than a threshold (24h default,
   comfortably beyond the outbox processor's own maximum retry-exhaustion
   time) and deletes them. Not run automatically - an operator/cron
   invokes it, same pattern as `repositories.outbox_repo.
   reclaim_stuck_processing`.
2. **DB commits (row + outbox event), processor dies before the rename.**
   The outbox event sits `'processing'` - recovered by the same automatic
   `reclaim_stuck_processing` mechanism Phase 6 built for SMS
   (`services/outbox_processor.py::RECLAIM_STALE_AFTER`, keyed off
   `claimed_at`). No document-specific code was needed for this window;
   it's inherited for free from the shared outbox infrastructure.
3. **Rename succeeds, processor dies before the DB status update.** The
   file is correctly at its final path, but the row still says
   `'pending'` and the outbox event still says `'processing'`. Recovered
   the same way as window 2 (reclaim -> re-claim -> re-run the handler);
   `finalize`'s idempotency (checksum match -> success without re-moving
   anything) makes the re-run safe -
   `tests/test_document_lifecycle.py::
   test_finalize_retry_after_final_file_already_exists_with_matching_checksum_is_a_safe_no_op`.
4. **Processor retries after any of the above.** Covered by the same
   idempotent-finalize behavior - a duplicate finalize attempt on an
   already-finalized file is a safe no-op, never a re-move, overwrite, or
   corruption.

Unlike the SMS conversion, there is **no equivalent to Window B's
"external call succeeded but we don't know it"** duplicate-risk here -
`finalize`'s rename either happens or it doesn't (a local filesystem
operation, not a third-party network call with an ambiguous outcome), and
checking "does the final file already exist with the right checksum"
before acting makes every retry path fully idempotent, not merely
at-least-once. Document finalization is exactly-once-effective even
though the underlying event delivery is still at-least-once (the
handler may run more than once, but only the first run ever actually
moves a file).

## Download/read path

No dedicated byte-serving download route exists for `documents` table
rows in this codebase (a separate, unrelated attachment subsystem -
`application/attachments/`, `api/routers/attachments.py` - serves
Operations Inbox **email** attachment bytes via an opaque
`attachment_ref`; it is untouched by this phase and stores its files
differently). `pages_app/documents.py`/`pages_app/dispatch_board.py`
only ever display `documents` metadata in a table - `status` is now part
of that metadata, so a `'pending'`/`'failed'` document is visibly
distinguishable from an `'available'` one rather than looking identical
to a normal attached document. If a byte-serving download route for this
`documents` table is added later, it must check `status == 'available'`
before serving content - not built in this phase since no such route
exists yet to guard.

## Cleanup

- **Orphaned staging files** (crash window 1): `reclaim_orphaned_staging_files`,
  operator/cron-invoked, described above.
- **`'failed'` document rows**: no automatic cleanup or resubmission
  built in this phase - `status='failed'` rows are left for an operator
  to inspect (`select * from documents where status = 'failed'`) and
  decide (delete the row, or have the user re-upload). The outbox side
  of a failed document finalize is inspectable/retryable via
  `scripts/process_outbox.py inspect`/`retry` (Phase 6's operator
  tooling, reused as-is - no document-specific CLI was needed).

## Tests

`tests/test_document_lifecycle.py` (storage-service unit tests on a real
`tmp_path` filesystem, `repositories/document_repo.py` SQLite atomicity
tests, `attach_load_document` authorization/wiring tests) plus
`tests/test_outbox_processor.py`'s three `document.file.finalize`
handler/projector tests (success -> `'available'`, retry -> stays
`'pending'`, terminal failure -> `'failed'`). See each test's docstring
for which specific requirement/crash-window it proves.
