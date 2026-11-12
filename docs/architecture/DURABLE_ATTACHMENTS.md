# Durable Attachment Storage (PR #19 Stage 2 / Task A)

## Problem

`inbox.sync` saves each email attachment to runner-local disk
(`DOCUMENT_STORAGE_DIR`, default `storage/load_documents/operations_inbox/`) and
enqueues an `inbox.process_message` job. The actual parse/persist happens in a
**later** worker invocation — and GitHub-Actions runners are ephemeral, so the
local files are gone by then. Stage 1 bridged this by base64-embedding the bytes
into `worker_jobs.payload` (bounded 3 MB/file, 8 MB/message), rehydrated and
SHA-256-verified at process time, fail-closed. Durable, but it keeps document
bodies inside the jobs table indefinitely (backup/replication bloat).

## Stage 2 backend: private object storage

When configured, attachment bytes live in a **private Supabase Storage bucket**;
the job payload carries only a stable, content-addressed object key plus the
SHA-256. No base64 body in `worker_jobs`.

- **Producer** (`workers/inbox_handlers.py::store_attachments_durably`, used by
  both live sync and `scripts/backfill_inbox.py`): uploads each attachment's
  bytes under `operations_inbox/<sha[:2]>/<sha256>` (content-addressed →
  idempotent re-upload on job retry, identical attachments dedupe), records
  `storage_key`, and keeps **no** `_content_b64`.
- **Consumer** (`workers/inbox_handlers.py::_rehydrate_attachment_files`): if the
  local file is missing/corrupted, downloads from the object store by key,
  verifies SHA-256, and writes it. **Fail-closed**: a missing object or a SHA
  mismatch raises `AttachmentDurabilityError`, so the job retries and then
  terminal-fails for an operator — a message is never persisted with a silently
  lost or wrong attachment.
- **Client** (`services/object_storage.py`): REST over `requests` (no new
  dependency, no SDK). Bounded retry with backoff on transient (connection,
  timeout, 5xx); hard failures raise `ObjectStorageError`.

### Durability is a prerequisite to enqueue (blocker 3)
`store_attachments_durably` runs **before** the `inbox.process_message` job is
enqueued and never leaves a saved attachment silently runner-local-only. Each
attachment ends up `durable=True` with either a `storage_key` or a bounded
`_content_b64`; anything that cannot be made durable raises
`AttachmentDurabilityError` and the producer **skips enqueuing** that message
(counted, surfaced; dedupe leaves it for a later run) instead of enqueuing a job
doomed to fail at rehydrate.
- **Oversized** (> `_ATTACHMENT_STORE_HARD_CAP`, 50 MB, the bucket limit; or over
  the base64 budget when storage is unconfigured): explicit `AttachmentDurabilityError`.
- **Missing bytes** for a saved attachment: explicit failure.
- **Corrupted** (on-disk or restored): SHA-256 verification fails closed.
- **Transient upload failure**: degrades to the bounded base64 copy (still
  durable), or raises if it won't fit.

## Read lifecycle (blocker 2)

Preview / download / reparse resolve bytes durably via
`operations_attachment_core.resolve_attachment_bytes`: local file first
(SHA-256-verified), else download from the object store by `storage_key` and
SHA-256-verify; `AttachmentResolutionError` (fails closed) otherwise. Wired into
the API (`GET /api/v1/attachments/{ref}/content`), the Streamlit preview/download
panel, and the "Parse / Reparse / Repair" action. `storage_key` and
`content_sha256` are carried on the internal `AttachmentMeta` but are **never**
serialized to an API client (stripped in `AttachmentSummaryOut.from_domain`
alongside `file_path`).

## No base64 bodies persisted (blocker 4)

`_content_b64` is stripped before attachment metadata is written to
`order_intake.parsed_data` (`strip_attachment_runtime_bytes`), and a completed
job's payload is scrubbed in the same transaction as `mark_completed`
(`worker_job_repo.scrub_completed_payload_bytes`) — never on a pending/processing
job, so an in-flight retry's base64 fallback is preserved. Legacy rows are
cleaned by `scripts/scrub_attachment_base64.py` (idempotent, `--dry-run`,
completed-jobs-only).

## Authorization, scoping, retention (blocker 5)

- The bucket **must be private**. All access is server-side via the authenticated
  `/storage/v1/object` endpoints with the **service-role** key — never the
  `/public/` path, never the anon key. `object_storage.assert_bucket_private`
  verifies this at startup/health; clients keep referencing documents by the
  opaque `attachment_ref`, never by object key or a public URL.
- **Tenant/account-scoped keys**: `inbox/<tenant>/<account-token>/<sha[:2]>/<sha>`.
  Tenant from `INBOX_TENANT_ID`; account is a short hash of the mailbox account
  (isolation without embedding the address). Content-addressed within an account
  → idempotent re-upload + dedupe.
- **Reference-aware deletion**: `services/attachment_retention.py` deletes an
  object only when no active record references it (an `order_intake.parsed_data`
  row, or a non-terminal `worker_jobs` payload). Retention duration is a bucket
  TTL/lifecycle policy; deletion is always explicit, never implicit.

## Required external configuration

Set all three (absent → `get_object_storage()` returns `None` and the system
falls back to the Stage 1 base64 stopgap, so nothing breaks):

| Secret | Value |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | service-role key (secret; **not** the anon key) |
| `INBOX_ATTACHMENTS_BUCKET` | private bucket name (default `operations-inbox-attachments`) |

For the GitHub Actions worker, add these as repository secrets alongside the
existing `DATABASE_URL` / mailbox secrets (see `WORKER_RUNTIME.md`).

## Migration considerations

- **No DB migration required.** Durability metadata (`storage_key`,
  `content_sha256`, `durable`) lives in the existing `worker_jobs.payload`
  (jsonb) and the attachment metadata dicts — no new columns, no schema change.
- **One-time setup** (not performed by this change; the operator does it, this
  code never touches production): create the private bucket; optionally set a
  retention/TTL lifecycle policy on it.
- **Backward compatible.** In-flight jobs enqueued before this change carry
  `_content_b64` and still rehydrate from it. New jobs use the object store when
  configured, base64 otherwise.

## Retention

Objects are content-addressed, so uploads are idempotent. Long-term retention is
a **bucket lifecycle** concern — configure a TTL/expiry policy on the bucket.
`SupabaseObjectStorage.delete()` exists for explicit operator-driven purge (e.g.
when removing historical mail); nothing deletes implicitly during processing.
