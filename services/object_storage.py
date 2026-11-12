# services/object_storage.py

from __future__ import annotations

"""Durable object storage for operations-inbox attachments (PR #19 Stage 2 /
Task A).

Framework-neutral: no streamlit, no DB - imported by workers/inbox_handlers.py
and scripts/backfill_inbox.py. Built on `requests` (already a core dependency);
no new package, no SDK.

WHY. Attachments are saved to runner-local disk (DOCUMENT_STORAGE_DIR), which a
GitHub-Actions runner destroys between runs, so an inbox.process_message job
run in a LATER invocation finds the files gone. Stage 1 bridged this by base64-
embedding the bytes into worker_jobs.payload - durable, but it bloats the jobs
table and keeps document bodies in Postgres indefinitely. This module is the
Stage 2 backend: the bytes live in a private object store (Supabase Storage);
the job payload carries only a stable object key + SHA-256, never the bytes.

AUTHORIZATION. The bucket MUST be private. All access is server-side with the
service-role key via the authenticated /storage/v1/object endpoints - never the
/public/ path, never an anon key. Clients continue to reference documents by
the existing opaque attachment_ref, never by object key or a public URL, so a
tenant cannot reach another's documents.

CONFIGURATION (all required to activate; absent -> get_object_storage() returns
None and callers fall back to the base64 stopgap, so the system still runs with
zero external infrastructure):
  SUPABASE_URL                e.g. https://<project-ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY   service-role key (NOT the anon key); secret
  INBOX_ATTACHMENTS_BUCKET    private bucket name (default: operations-inbox-attachments)

RETENTION. Objects are content-addressed, so re-uploads are idempotent. Long-
term retention is a bucket lifecycle concern (configure a TTL/expiry policy on
the bucket in Supabase); delete() is provided for explicit cleanup of a
message's objects when an operator purges historical mail. Nothing here deletes
on its own - removal is deliberate, never implicit.
"""

import logging
import time
from typing import Protocol, runtime_checkable

import requests

from config import get_secret

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "operations-inbox-attachments"
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 0.5
_TRANSIENT_STATUS = {500, 502, 503, 504}


class ObjectStorageError(Exception):
    """A durable object-storage operation failed (transport, auth, missing
    object, or exhausted retries). Raised so an inbox.process_message job fails
    and retries (then terminal-fails for an operator) rather than silently
    proceeding with a lost attachment."""


@runtime_checkable
class ObjectStorage(Protocol):
    def upload(self, key: str, content: bytes, *, content_type: str = ...) -> None: ...
    def download(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class SupabaseObjectStorage:
    """Supabase Storage over its REST API. Private-bucket, service-role-only.

    `session` is injectable so tests exercise the HTTP contract with a fake and
    no network. Transient failures (connection errors, timeouts, 5xx) are
    retried with a short backoff; a hard failure raises ObjectStorageError and
    lets the worker job's own retry/terminal-fail machinery take over."""

    def __init__(
        self,
        *,
        base_url: str,
        bucket: str,
        token: str,
        session: requests.Session | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket
        self._token = token
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_retries = max(1, int(max_retries))

    def _object_url(self, key: str) -> str:
        return f"{self._base_url}/storage/v1/object/{self._bucket}/{key}"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request_with_retry(self, method: str, url: str, **kwargs):
        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
            except requests.RequestException as exc:  # connection/timeout - transient
                last_error = type(exc).__name__
            else:
                if resp.status_code not in _TRANSIENT_STATUS:
                    return resp
                last_error = f"HTTP {resp.status_code}"
            if attempt < self._max_retries:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
        raise ObjectStorageError(f"{method} object failed after {self._max_retries} attempts ({last_error})")

    def upload(self, key: str, content: bytes, *, content_type: str = "application/octet-stream") -> None:
        headers = {
            **self._auth_headers(),
            "Content-Type": content_type,
            # Idempotent: a retried job re-uploading the same content-addressed
            # key must overwrite, not 400 on an already-existing object.
            "x-upsert": "true",
        }
        resp = self._request_with_retry("POST", self._object_url(key), data=bytes(content), headers=headers)
        if resp.status_code >= 400:
            raise ObjectStorageError(f"upload {key} failed: HTTP {resp.status_code}")

    def download(self, key: str) -> bytes:
        resp = self._request_with_retry("GET", self._object_url(key), headers=self._auth_headers())
        if resp.status_code == 200:
            return resp.content
        raise ObjectStorageError(f"download {key} failed: HTTP {resp.status_code}")

    def exists(self, key: str) -> bool:
        url = f"{self._base_url}/storage/v1/object/info/{self._bucket}/{key}"
        resp = self._request_with_retry("GET", url, headers=self._auth_headers())
        return resp.status_code == 200

    def delete(self, key: str) -> None:
        """Explicit removal - for operator-driven retention/purge only, never
        called implicitly during normal processing."""
        resp = self._request_with_retry("DELETE", self._object_url(key), headers=self._auth_headers())
        if resp.status_code >= 400 and resp.status_code != 404:
            raise ObjectStorageError(f"delete {key} failed: HTTP {resp.status_code}")

    def bucket_is_private(self) -> bool:
        """True iff the configured bucket is PRIVATE (not public). Attachments
        are customer documents; a public bucket would expose them by URL without
        the service-role key. Used by assert_bucket_private at startup/health."""
        import json as _json

        url = f"{self._base_url}/storage/v1/bucket/{self._bucket}"
        resp = self._request_with_retry("GET", url, headers=self._auth_headers())
        if resp.status_code != 200:
            raise ObjectStorageError(f"bucket info for {self._bucket} failed: HTTP {resp.status_code}")
        try:
            info = _json.loads(resp.content or getattr(resp, "text", "") or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ObjectStorageError(f"bucket info for {self._bucket} could not be parsed: {exc}") from exc
        return not bool(info.get("public"))


def assert_bucket_private(storage: SupabaseObjectStorage | None) -> None:
    """Fail loudly if the attachments bucket is public. A no-op when object
    storage is not configured. Call from a startup/health check - never serve or
    store customer documents in a public bucket."""
    if storage is None:
        return
    if not storage.bucket_is_private():
        raise ObjectStorageError(
            "INBOX_ATTACHMENTS_BUCKET must be a PRIVATE Supabase Storage bucket; it is currently public."
        )


def get_object_storage() -> SupabaseObjectStorage | None:
    """Build the configured object-storage client, or None when the required
    configuration is absent (callers then fall back to the base64 stopgap).
    Read fresh so configuration changes take effect without a code change."""
    base_url = get_secret("SUPABASE_URL")
    token = get_secret("SUPABASE_SERVICE_ROLE_KEY")
    if not base_url or not token:
        return None
    bucket = get_secret("INBOX_ATTACHMENTS_BUCKET") or DEFAULT_BUCKET
    return SupabaseObjectStorage(base_url=base_url, bucket=bucket, token=token)
