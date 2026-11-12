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

### Explicit handling of edge cases
- **Oversized** (> `_ATTACHMENT_STORE_PER_FILE_CAP`, 25 MB): not uploaded, flagged
  `durable=False`, stays same-run-only. Not silently uploaded unbounded.
- **Missing bytes** (save errored): left disk-only, no durable copy claimed.
- **Corrupted** (on-disk or restored): SHA-256 verification fails closed.
- **Transient upload failure**: degrades to the base64 stopgap for that one
  attachment rather than failing the whole sync.

## Authorization

The bucket **must be private**. All access is server-side via the authenticated
`/storage/v1/object` endpoints with the **service-role** key — never the
`/public/` path, never the anon key. API/UI clients keep referencing documents by
the existing opaque `attachment_ref`, never by object key or a public URL.

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
