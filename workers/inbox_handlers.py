# workers/inbox_handlers.py

from __future__ import annotations

"""Phase 7: Inbox job handlers, registered in workers/processor.py::
JOB_HANDLERS. Kept out of workers/processor.py itself so the generic
processor stays free of email-specific branching (Phase 7 design
principle) - this module is the "future side effect" integration point
processor.py's JOB_HANDLERS docstring describes.

Wraps services.operations_inbox_service's existing, unchanged functions
(sync fetch/dedup logic, the message-processing pipeline) - Phase 7
reuses this codebase's proven parsing/sync/classification pipeline, it
does not rewrite it (see docs/architecture/WORKER_RUNTIME.md and
CLAUDE.md's "do not rewrite proven parsing algorithms" rule).

services.operations_inbox_service::sync_operations_email_engine itself
is UNCHANGED and still called directly by the Streamlit "Sync Email
Engine" button (pages_app/operations_inbox.py) and the admin debug sync
(pages_app/email_imports.py) - this module's handle_inbox_sync no longer
calls it (see below); the two paths are now independent, and neither's
behavior depends on the other.
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Attachment durability across ephemeral GitHub-Actions runners: the files
# _save_operations_email_attachments writes live on the runner's local disk,
# which is destroyed between runs, so an inbox.process_message job processed in
# a LATER run would find them gone. To survive without new infrastructure, a
# bounded base64 copy of each saved attachment's bytes rides along in the
# durable worker_jobs.payload (PostgreSQL); the process handler rehydrates any
# missing file from it before parsing. Bounded so a huge attachment can't bloat
# the jobs table - oversized ones fall back to the same-run-only disk file.
_ATTACHMENT_EMBED_PER_FILE_CAP = 3 * 1024 * 1024  # 3 MB raw per attachment
_ATTACHMENT_EMBED_TOTAL_CAP = 8 * 1024 * 1024  # 8 MB raw per message

# Hard per-file ceiling for the object store (Stage 2 / Task A): matches
# Supabase Storage's default per-object upload limit. An attachment larger than
# this cannot be made durable in the bucket, so it is an EXPLICIT durability
# failure (see store_attachments_durably) - never a silent same-run-only drop.
_ATTACHMENT_STORE_HARD_CAP = 50 * 1024 * 1024  # 50 MB raw per attachment


def _attachment_bytes_by_sha(message: dict) -> dict:
    """Map content_sha256 -> raw bytes for every attachment carrying bytes on
    the message. Shared by the base64 (embed) and object-store (upload) paths."""
    import hashlib

    by_sha: dict[str, bytes] = {}
    for att in message.get("attachments") or []:
        content = att.get("content")
        if isinstance(content, (bytes, bytearray)) and content:
            by_sha[hashlib.sha256(bytes(content)).hexdigest()] = bytes(content)
    return by_sha


def _tenant_slug() -> str:
    """Tenant segment for object keys. Single-tenant today (one company), but
    scoping keys by tenant keeps the store partitioned so a future multi-tenant
    deployment cannot let one tenant's key collide with or reach another's."""
    import re

    from config import get_secret

    raw = str(get_secret("INBOX_TENANT_ID") or "calitrans").strip().lower()
    return re.sub(r"[^a-z0-9_-]+", "-", raw) or "calitrans"


def _account_token(mailbox_account: str) -> str:
    """Account segment for object keys - a short hash of the mailbox account so
    keys are account-scoped (isolation) without embedding the email address in
    the object path. Empty account -> a stable 'shared' bucketing token."""
    import hashlib

    account = str(mailbox_account or "").strip().lower()
    if not account:
        return "shared"
    return hashlib.sha256(account.encode("utf-8")).hexdigest()[:12]


def _attachment_object_key(content_sha256: str, *, tenant: str, account: str) -> str:
    """Tenant/account-scoped, content-addressed, sharded object key.
    Deterministic (same bytes + same account -> same key), so re-uploads on a
    retried job are idempotent and identical attachments within one account
    dedupe. No filename in the path (avoids key-injection); the filename rides in
    the payload metadata. Scoping by tenant+account prevents cross-account access
    via key guessing."""
    sha = str(content_sha256 or "")
    return f"inbox/{tenant}/{account}/{sha[:2]}/{sha}"


# Sentinel: distinguishes "storage argument not supplied, resolve from config"
# from "explicitly no storage" (storage=None -> base64 fallback).
_STORAGE_UNSET = object()


def _embed_one_or_raise(item: dict, content: bytes, embedded_total: int) -> tuple[dict, int]:
    """Attach a bounded base64 copy of `content` to `item` (the no-object-store
    durability path). Raises AttachmentDurabilityError when the attachment is too
    large for the per-file or per-message base64 budget - there is then NO way to
    make it durable without object storage, so it is an explicit failure, never a
    silent same-run-only drop (Task A blocker 3)."""
    import base64

    if len(content) > _ATTACHMENT_EMBED_PER_FILE_CAP or embedded_total + len(content) > _ATTACHMENT_EMBED_TOTAL_CAP:
        raise AttachmentDurabilityError(
            f"attachment {item.get('filename') or item.get('content_sha256', '')[:8]} "
            "is too large to persist durably without object storage; configure object storage "
            "(SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/INBOX_ATTACHMENTS_BUCKET)"
        )
    item["_content_b64"] = base64.b64encode(content).decode("ascii")
    item["durable"] = True
    return item, embedded_total + len(content)


def store_attachments_durably(pre_saved_attachments: list, message: dict, *, storage=_STORAGE_UNSET) -> list:
    """Make every saved attachment durable across ephemeral runners BEFORE its
    inbox.process_message job is enqueued, and return enriched metadata for the
    payload. Durability is a PREREQUISITE: this never leaves a saved attachment
    silently runner-local-only (Task A blocker 3). Each attachment ends up with
    either a `storage_key` (object storage) or a bounded `_content_b64` (the
    no-infrastructure fallback), both flagged `durable=True`; anything that
    cannot be made durable raises AttachmentDurabilityError so the producer skips
    enqueuing a doomed job and surfaces the problem instead.

    With object storage configured: upload each attachment's bytes to the
    private bucket under a content-addressed key, record `storage_key`, keep NO
    base64 in the payload. A transient upload failure degrades to the bounded
    base64 fallback (still durable); a hard failure or an attachment over the
    bucket's size limit raises. Without object storage: base64 within budget, or
    raise. An entry with no real saved file/hash (a parse-error placeholder) and
    no retrievable bytes carries nothing to lose and passes through untouched."""
    from services.object_storage import ObjectStorageError, get_object_storage

    store = get_object_storage() if storage is _STORAGE_UNSET else storage
    by_sha = _attachment_bytes_by_sha(message)
    tenant = _tenant_slug()
    account = _account_token(message.get("mailbox_account"))
    embedded_total = 0
    enriched = []
    for saved in pre_saved_attachments or []:
        item = dict(saved)
        item.pop("_content_b64", None)
        item.pop("durable", None)
        sha = str(item.get("content_sha256") or "")
        file_path = str(item.get("file_path") or "")

        if not file_path or not sha:
            # Not a real durable attachment (e.g. a parse-error placeholder) -
            # nothing was persisted, nothing to lose. Pass through.
            enriched.append(item)
            continue

        content = by_sha.get(sha)
        if not content:
            # A saved attachment whose bytes we can no longer see cannot be made
            # durable across runners - explicit, never silent local-only.
            raise AttachmentDurabilityError(
                f"attachment {item.get('filename') or sha[:8]} has no retrievable bytes to persist durably"
            )

        if store is not None:
            if len(content) > _ATTACHMENT_STORE_HARD_CAP:
                raise AttachmentDurabilityError(
                    f"attachment {item.get('filename') or sha[:8]} exceeds the object-store per-file limit "
                    f"({_ATTACHMENT_STORE_HARD_CAP} bytes) and cannot be stored durably"
                )
            key = _attachment_object_key(sha, tenant=tenant, account=account)
            content_type = str(item.get("content_type") or "application/octet-stream")
            try:
                store.upload(key, content, content_type=content_type)
            except ObjectStorageError:
                # Storage hiccup: fall back to the bounded base64 copy so the
                # attachment stays durable (or raise if it won't fit). Logged
                # without any filename/body/credential.
                logger.warning("attachment durable upload failed; using base64 fallback for one attachment")
                item, embedded_total = _embed_one_or_raise(item, content, embedded_total)
            else:
                item["storage_key"] = key
                item["durable"] = True
        else:
            item, embedded_total = _embed_one_or_raise(item, content, embedded_total)

        enriched.append(item)
    return enriched


def _embed_attachment_content(pre_saved_attachments: list, message: dict) -> list:
    """Return pre_saved metadata with a bounded base64 copy of each saved
    attachment's bytes (`_content_b64`) so the file can be rehydrated on a
    different runner. Matches saved metadata to message attachments by
    content_sha256; silently leaves `_content_b64` absent when bytes are
    unavailable or over the cap (that attachment stays same-run-only)."""
    import base64

    by_sha = _attachment_bytes_by_sha(message)

    embedded_total = 0
    enriched = []
    for saved in pre_saved_attachments or []:
        item = dict(saved)
        sha = str(item.get("content_sha256") or "")
        content = by_sha.get(sha)
        if (
            content
            and len(content) <= _ATTACHMENT_EMBED_PER_FILE_CAP
            and embedded_total + len(content) <= _ATTACHMENT_EMBED_TOTAL_CAP
        ):
            item["_content_b64"] = base64.b64encode(content).decode("ascii")
            embedded_total += len(content)
        enriched.append(item)
    return enriched


class AttachmentDurabilityError(Exception):
    """A saved attachment could not be durably restored and SHA-256 verified.
    Raised so the inbox.process_message job fails (retries, then terminal-fails
    for an operator) rather than silently persisting a message with a missing
    or corrupted attachment."""


def _durable_bytes_for(saved: dict, store) -> bytes | None:
    """Fetch an attachment's durable bytes for restoration, trying the object
    store (by `storage_key`) first, then the base64 copy embedded in the
    payload. Returns None when no durable copy exists. A storage transport
    failure is swallowed to None so the base64 fallback (if any) can still be
    tried; a bytes/SHA mismatch is NOT swallowed here - the caller verifies and
    fails closed."""
    import base64

    storage_key = str(saved.get("storage_key") or "")
    if storage_key and store is not None:
        from services.object_storage import ObjectStorageError

        try:
            return store.download(storage_key)
        except ObjectStorageError:
            pass  # fall through to base64, then to fail-closed in the caller

    content_b64 = saved.get("_content_b64")
    if content_b64:
        return base64.b64decode(content_b64)
    return None


def _rehydrate_attachment_files(pre_saved_attachments: list, *, storage=_STORAGE_UNSET) -> int:
    """Ensure every saved attachment is present on disk and matches its
    recorded SHA-256, restoring from the durable copy - object storage
    (`storage_key`) first, then the base64 copy in the job payload - when the
    local file is gone (fresh runner) or corrupted. FAIL-CLOSED: if an
    attachment file is missing or fails verification and there is no durable
    copy to restore it from (or the restored bytes fail SHA-256), raise
    AttachmentDurabilityError - never proceed with the attachment silently lost
    or wrong. Returns the number of files restored."""
    import hashlib
    from pathlib import Path

    from services.object_storage import get_object_storage

    store = get_object_storage() if storage is _STORAGE_UNSET else storage

    restored = 0
    for saved in pre_saved_attachments or []:
        file_path = str(saved.get("file_path") or "")
        if not file_path:
            # No durable file was ever produced for this entry (e.g. the save
            # itself errored and recorded a parse_error) - nothing to restore.
            continue

        expected_sha = str(saved.get("content_sha256") or "")
        path = Path(file_path)

        if not expected_sha:
            # No recorded hash (malformed/legacy/parse-error entry) - a real
            # saved attachment always records content_sha256, so this is not a
            # verifiable attachment to fail the message over. Best-effort:
            # restore from a durable copy if the file is gone, else leave it.
            if not path.exists():
                data = _durable_bytes_for(saved, store)
                if data is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    restored += 1
            continue

        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha:
                continue  # present and verified
            # On-disk file is corrupted - fall through to restore from the copy.

        data = _durable_bytes_for(saved, store)
        if data is None:
            raise AttachmentDurabilityError(
                f"attachment {path.name} is missing/corrupted and no durable copy is available"
            )
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise AttachmentDurabilityError(f"restored attachment {path.name} failed SHA-256 verification")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        restored += 1
    return restored

# Recorded as the `actor` on inbox.process_message jobs enqueued by the
# worker itself - Phase 7 STEP 22: background processing is system
# execution of previously-authorized work (request_inbox_sync already
# ran require_permission for the human who triggered the sync this job
# chain descends from), not a fresh user-triggered mutation, so it must
# not be attributed to a hardcoded human role like "dispatcher".
SYSTEM_ACTOR_INBOX_WORKER = "system:inbox-worker"


def _fetch_failure_reason(diagnostics: dict, *, fetched_count: int = 0) -> str:
    """Infrastructure-failure reason for a sync fetch, or "" when the fetch
    itself succeeded. Distinguishes "the inbox.sync job ran" from "a mailbox
    was actually retrieved" (the P2 observability gap): a job that logged
    into ZERO mailboxes must fail (bad credentials / connectivity) rather
    than complete as a healthy empty inbox, so the failure is visible via the
    worker job's status + sanitized last_error instead of a silently
    "completed" sync that fetched nothing.

    - fetched_count > 0 -> messages came back, so a mailbox was unambiguously
      retrieved; success regardless of per-account diagnostic flags.
    - accounts_attempted == 0 with errors -> no mailbox could even be
      attempted (unchanged behavior).
    - accounts_attempted == 0 with no errors -> nothing configured; left a
      no-op success, as before (not this change's concern).
    - accounts_attempted > 0, nothing fetched, and zero successful logins ->
      every attempted mailbox login failed; now a failure (previously
      misreported success).
    - at least one successful login -> success (an empty inbox is healthy)."""
    if fetched_count > 0:
        return ""

    errors = diagnostics.get("errors") or []
    accounts_attempted = int(diagnostics.get("accounts_attempted", 0) or 0)
    successful_logins = sum(
        1 for acct in (diagnostics.get("per_account") or []) if acct.get("login_success")
    )

    if accounts_attempted == 0:
        if errors:
            return (
                "No operations email mailbox could be attempted. "
                "Check email addresses and Yahoo app passwords in secrets."
            )
        return ""

    if successful_logins == 0:
        return (
            "Every attempted mailbox login failed - no mailbox was successfully "
            "retrieved. Check Yahoo app passwords in secrets."
        )

    return ""


def handle_inbox_sync(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.sync' (application/inbox/commands.py::
    request_inbox_sync).

    STEP 16/17: fetches messages and enqueues one inbox.process_message
    job per new message - it does NOT process messages inline anymore
    (that was this handler's batch-3 behavior, safe to change since
    nothing in production depended on it yet - see docs/architecture/
    WORKER_RUNTIME.md). Delegates to _fetch_and_enqueue_inbox_messages
    below rather than services.operations_inbox_service::
    sync_operations_email_engine, which still does full inline processing
    for the Streamlit button's benefit and is otherwise untouched.

    Returns (success, error) per workers/processor.py's handler contract:
    success=False must mean the fetch's own infrastructure failed
    (mailbox unreachable, missing credentials, an uncaught exception) -
    not that some individual messages couldn't be enqueued (counted,
    recorded, fetch continues - see _fetch_and_enqueue_inbox_messages).

    payload may override `limit`/`time_budget_seconds`; defaults match
    sync_operations_email_engine's own interactive defaults (docs/
    OPERATIONS_INBOX_REQUIREMENTS.md's Email Sync Requirements section:
    8-12 messages, 20-30s time budget)."""
    limit = int(payload.get("limit", 12))
    time_budget_seconds = int(payload.get("time_budget_seconds", 25))

    result = _fetch_and_enqueue_inbox_messages(limit=limit, time_budget_seconds=time_budget_seconds)

    error = str(result.get("error") or "")
    return (not error), error


def _fetch_and_enqueue_inbox_messages(*, limit: int = 12, time_budget_seconds: int = 25) -> dict:
    """Fetch-only half of the Phase 7 sync/process split (STEP 16/17).
    Same fetch + dedup logic services.operations_inbox_service::
    sync_operations_email_engine already uses (same functions, called the
    same way, unchanged) - the difference is what happens to each new
    message: enqueue an inbox.process_message job, instead of processing
    it inline.

    Attachments are saved to disk HERE, at enqueue time (reusing
    services.operations_inbox_service::_save_operations_email_attachments,
    the exact function the inline path already calls - not a second
    implementation), not deferred to the inbox.process_message job.
    Raw attachment bytes cannot safely travel through worker_jobs.payload
    (jsonb): Phase 6B's design doc already rejected carrying file bytes
    in a job payload for exactly this "bloats the table, becomes a
    backup/replication burden" reason, and
    repositories.worker_job_repo.enqueue_job's json.dumps(...,
    default=str) would silently mangle raw bytes into an unusable string
    rather than error loudly. The job payload carries only the
    already-saved attachment metadata (filenames/paths/hashes - see
    services.operations_attachment_service::save_operations_attachment's
    return shape, which never includes raw content) plus the rest of the
    message with its `attachments` key removed.

    Each message's enqueue is its own short transaction (matches
    repositories/worker_job_repo.py's "conn is short-lived, one per
    logical write" convention) - a time-budget cutoff mid-loop still
    preserves every enqueue already committed, not just the ones from a
    single all-or-nothing batch."""
    from db_client import transaction
    from repositories.worker_job_repo import enqueue_job
    from services.operations_inbox_service import (
        _email_sync_unique_message_id,
        _save_operations_email_attachments,
        compute_fetch_time_budget,
        compute_insert_loop_deadline,
        ensure_operations_email_sync_schema,
        load_existing_operations_email_lookup,
        operations_email_already_imported,
        safe_str,
    )

    started = time.perf_counter()
    try:
        time_budget_seconds = max(5, int(time_budget_seconds or 25))
    except Exception:
        time_budget_seconds = 25
    deadline = started + time_budget_seconds

    result: dict[str, Any] = {
        "fetched": 0,
        "inbound_fetched": 0,
        "outbound_fetched": 0,
        "enqueued": 0,
        "skipped": 0,
        "errors": 0,
        "elapsed_seconds": None,
        "stopped_early": False,
        "error": "",
        "error_messages": [],
    }

    try:
        ensure_operations_email_sync_schema()
        from services import email_client

        elapsed_before_fetch = time.perf_counter() - started
        fetch_time_budget = compute_fetch_time_budget(time_budget_seconds, elapsed_before_fetch)
        messages = (
            email_client.fetch_operations_email_sync(limit=int(limit or 12), time_budget_seconds=fetch_time_budget)
            or []
        )
        diagnostics = email_client.get_last_operations_email_sync_diagnostics()
        result["diagnostics"] = diagnostics
        result["successful_logins"] = sum(
            1 for acct in (diagnostics.get("per_account") or []) if acct.get("login_success")
        )
        result["error"] = _fetch_failure_reason(diagnostics, fetched_count=len(messages))
        result["fetched"] = len(messages)
        result["inbound_fetched"] = sum(
            1 for item in messages if safe_str(item.get("direction")).lower() != "outbound"
        )
        result["outbound_fetched"] = sum(
            1 for item in messages if safe_str(item.get("direction")).lower() == "outbound"
        )

        lookup_limit = max(1000, min(3000, int(limit or 12) * 40))
        existing_lookup = load_existing_operations_email_lookup(limit=lookup_limit)
        enqueue_deadline = compute_insert_loop_deadline(deadline, time.perf_counter())

        # Per-mailbox skip/queue tallies, keyed by the sanitized alias carried
        # on each message's `mailbox` field - used for the per-mailbox log
        # lines emitted after the loop (see _log_per_mailbox_sync).
        per_mailbox_counts: dict[str, dict[str, int]] = {}

        def _bump(alias: str, key: str) -> None:
            per_mailbox_counts.setdefault(alias, {"skipped": 0, "queued": 0})[key] += 1

        for message in messages:
            subject = safe_str(message.get("subject")) or "(no subject)"
            sender = safe_str(message.get("from")) or safe_str(message.get("sender")) or "(unknown sender)"
            received_at = safe_str(message.get("received_at")) or None
            message_id = _email_sync_unique_message_id(message)
            alias = email_client.operations_mailbox_alias(message.get("mailbox"))

            if time.perf_counter() >= enqueue_deadline:
                result["stopped_early"] = True
                result["error_messages"].append(
                    "Email fetch-and-enqueue stopped after reaching the time budget."
                )
                break

            if operations_email_already_imported(
                message_id, subject=subject, sender=sender, received_at=received_at, lookup=existing_lookup
            ):
                result["skipped"] += 1
                _bump(alias, "skipped")
                continue

            try:
                pre_saved_attachments = _save_operations_email_attachments(message, message_id)
            except Exception as exc:
                pre_saved_attachments = []
                result["error_messages"].append(f"{subject[:80]}: attachment save failed: {exc}")

            job_message = {key: value for key, value in message.items() if key != "attachments"}

            # Durability is a prerequisite to enqueue (Task A blocker 3): if an
            # attachment cannot be made durable, DO NOT enqueue a job doomed to
            # fail at rehydrate time - skip this message (dedupe leaves it
            # un-imported so the next sync retries it) and surface the reason.
            try:
                pre_saved_attachments = store_attachments_durably(pre_saved_attachments, message)
            except AttachmentDurabilityError as exc:
                result["errors"] += 1
                result["error_messages"].append(f"{subject[:80]}: not durable, not enqueued: {exc}")
                continue

            try:
                with transaction() as conn:
                    enqueue_job(
                        conn=conn,
                        job_type="inbox.process_message",
                        aggregate_type="email_message",
                        aggregate_id=message_id,
                        payload={"message": job_message, "pre_saved_attachments": pre_saved_attachments},
                        idempotency_key=f"inbox.process_message:{message_id}",
                        actor=SYSTEM_ACTOR_INBOX_WORKER,
                    )
                result["enqueued"] += 1
                _bump(alias, "queued")
                existing_lookup.setdefault("by_message_id", {})[message_id.lower()] = {
                    "source_message_id": message_id
                }
            except Exception as exc:
                result["errors"] += 1
                result["error_messages"].append(f"{subject[:80]}: enqueue failed: {exc}")

        _log_per_mailbox_sync(diagnostics, per_mailbox_counts)

    except Exception as exc:
        result["error"] = str(exc)

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return result


def _log_per_mailbox_sync(diagnostics: dict, per_mailbox_counts: dict[str, dict[str, int]]) -> None:
    """One sanitized log line per mailbox: alias, login ok/failed (error type
    only), messages found, skipped-as-already-imported, and jobs queued.
    Never logs an address, subject, body, or password - only the role alias,
    a boolean-ish login state, an exception class name, and integer counts."""
    from services import email_client

    for account in diagnostics.get("per_account") or []:
        alias = email_client.operations_mailbox_alias(account.get("email"))
        counts = per_mailbox_counts.get(alias, {})
        login_ok = bool(account.get("login_success"))
        error_type = "" if login_ok else (str(account.get("error_type") or "") or "unknown")
        found = int(account.get("messages_found") or account.get("messages_fetched") or 0)
        logger.info(
            "inbox.sync mailbox=%s login=%s error_type=%s found=%d skipped_already_imported=%d jobs_queued=%d",
            alias,
            "ok" if login_ok else "failed",
            error_type,
            found,
            int(counts.get("skipped", 0)),
            int(counts.get("queued", 0)),
        )


def handle_inbox_process_message(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.process_message', enqueued by
    _fetch_and_enqueue_inbox_messages above (via handle_inbox_sync).

    payload shape: {"message": <message dict, no "attachments" key>,
    "pre_saved_attachments": <metadata list from
    services.operations_attachment_service::save_operations_attachment,
    no raw bytes>} - not the raw message dict directly (that changed in
    this batch; nothing in production depended on the old shape, since no
    producer existed before this batch either).

    Wraps services.operations_inbox_service::_insert_operations_email_
    message, passing pre_saved_attachments through so it skips
    re-deriving attachments from `message['attachments']` (which was
    deliberately stripped before this payload was ever built - see
    _fetch_and_enqueue_inbox_messages's docstring for why). This is the
    same function sync_operations_email_engine's own inline per-message
    loop calls, covering the full STEP 18 pipeline: latest-body
    extraction + CASE-010 segmentation, parse, attachment field merge,
    classification/triage, load matching (embedded in the triage/
    classification result), persist (INSERT into order_intake).

    After a successful insert, reconciles the touched conversation's
    status (services.operations_inbox_service::sync_conversation_status)
    - sync_operations_email_engine does the same thing, batched once
    across all touched conversations at the end of a whole sync run; here
    it happens once per processed message instead, since jobs process
    independently and a whole-sync-run boundary no longer exists in the
    async model. Best-effort, matching sync_operations_email_engine's own
    swallowed-failure behavior for this step (a reconciliation failure
    must not fail the whole job - the message itself was already
    successfully inserted).

    No try/except around the insert itself: _prepare_operations_email_
    record (called inside) already catches every parse/attachment/
    classification/triage failure internally and returns
    processing_errors instead of raising - proven by tests/
    test_operations_email_insert_resilience.py. Only the persist step's
    DB insert can still raise, and that IS a genuine infrastructure
    failure, correctly left to propagate to workers/processor.py's
    generic exception handling (retry, then terminal-fail) rather than
    caught and misreported as success here."""
    from services.operations_inbox_service import (
        HISTORICAL_BACKFILL_REVIEW_STATUS,
        _insert_operations_email_message,
        sync_conversation_status,
    )

    from services.inbox_backfill_policy import classify_message

    message = payload.get("message") or {}
    pre_saved_attachments = payload.get("pre_saved_attachments") or []
    # Durability: on a fresh runner the saved attachment files are gone -
    # restore them from the durable copy (object storage, then base64). Any
    # attachment that cannot be restored and verified fails the whole message
    # (raises) so it is never silently dropped - see _rehydrate_attachment_files.
    _rehydrate_attachment_files(pre_saved_attachments)

    # Flag-independent classification (Stage 2 / Task B + P1): historical-vs-live
    # is decided from the message's OWN received date vs the configured live
    # cutoff (services.inbox_backfill_policy.classify_message), NEVER from the
    # producer-stamped `backfill` payload flag. Live sync and backfill share one
    # idempotency key and enqueue_job's ON CONFLICT never rewrites the payload,
    # so a flag-driven decision let whichever producer enqueued first freeze the
    # outcome - a backfill of recent mail could bury a live, actionable email.
    #
    # INDETERMINATE (no valid cutoff, or an unparseable received date) resolves
    # to LIVE: the safe default never buries a potentially-actionable message
    # (worst case a human archives an old one). Backfill of genuinely historical
    # mail cannot reach this path - scripts/backfill_inbox.py refuses to run
    # without a configured cutoff, and real mail always carries a Date.
    #
    # Stable across retries AND cutoff-config changes: the decision persisted on
    # the order_intake row at first insert is authoritative - the insert's
    # ON CONFLICT (source_message_id) DO NOTHING means a later retry (even one
    # that recomputes differently after the cutoff moved) never overwrites it.
    decision = classify_message(message)
    is_historical = decision.is_historical
    if decision.is_indeterminate:
        logger.info("inbox.process_message classification=indeterminate->live reason=%s", decision.reason)

    # Historical isolation (atomic): a historical message must never surface as
    # live dispatch work. The row is INSERTED with the terminal historical
    # review_status in the same transaction - no separate post-insert update to
    # race or silently fail - so every active inbox queue excludes it even if
    # the worker crashes immediately after the insert commits.
    force_review_status = HISTORICAL_BACKFILL_REVIEW_STATUS if is_historical else None
    inserted = _insert_operations_email_message(
        message, pre_saved_attachments=pre_saved_attachments, force_review_status=force_review_status
    )

    # A historical message must never change LIVE conversation state: it is
    # inserted terminal (Historical (Backfill)) and excluded from active queues,
    # but sync_conversation_status recomputes the shared conversation's status
    # from all its rows and could flip a currently-active conversation (same
    # conversation_key) based on old mail. Reconcile status only for live
    # messages; historical mail deliberately leaves live conversations alone.
    conversation_key = inserted.get("conversation_key")
    if conversation_key and not is_historical:
        try:
            sync_conversation_status(conversation_key)
        except Exception:
            pass

    return True, ""
