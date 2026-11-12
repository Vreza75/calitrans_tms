"""PR #19 Stage 2 / Task A: durable attachment storage via object storage.

Replaces reliance on GitHub-Actions runner-local attachment files (and the
Stage 1 base64-in-worker_jobs.payload stopgap) with a durable object store
(Supabase Storage). These tests use a FAKE in-memory object store and a fake
HTTP session - no network, no real bucket, no credentials.
"""
from __future__ import annotations

import base64
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Config-gated factory
# ---------------------------------------------------------------------------

def test_factory_returns_none_when_unconfigured(monkeypatch):
    from services import object_storage

    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "INBOX_ATTACHMENTS_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    assert object_storage.get_object_storage() is None


def test_factory_builds_client_when_fully_configured(monkeypatch):
    from services import object_storage

    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("INBOX_ATTACHMENTS_BUCKET", "operations-inbox")
    client = object_storage.get_object_storage()
    assert isinstance(client, object_storage.SupabaseObjectStorage)


# ---------------------------------------------------------------------------
# SupabaseObjectStorage HTTP contract (fake session, no network)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeSession:
    """Records requests; returns queued responses (or 200 by default)."""

    def __init__(self):
        self.calls = []
        self._queue = []

    def queue(self, *responses):
        self._queue.extend(responses)

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self._queue:
            return self._queue.pop(0)
        return _FakeResponse(200, content=kwargs.get("data", b"") or b"")


def _client(session):
    from services.object_storage import SupabaseObjectStorage

    return SupabaseObjectStorage(
        base_url="https://proj.supabase.co",
        bucket="operations-inbox",
        token="service-role-key",
        session=session,
        max_retries=3,
    )


def test_upload_uses_authenticated_private_endpoint_never_public(monkeypatch):
    session = _FakeSession()
    client = _client(session)
    client.upload("operations_inbox/abc/def", b"bytes", content_type="application/pdf")

    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://proj.supabase.co/storage/v1/object/operations-inbox/operations_inbox/abc/def"
    assert call["headers"]["Authorization"] == "Bearer service-role-key"
    # Idempotent re-upload on job retry must not 400 on an existing object.
    assert call["headers"].get("x-upsert") == "true"
    assert "/public/" not in call["url"]  # never a public URL
    assert call["data"] == b"bytes"


def test_download_returns_bytes_from_authenticated_endpoint():
    session = _FakeSession()
    session.queue(_FakeResponse(200, content=b"the pdf bytes"))
    client = _client(session)
    data = client.download("operations_inbox/abc/def")
    assert data == b"the pdf bytes"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer service-role-key"


def test_upload_retries_transient_5xx_then_succeeds():
    session = _FakeSession()
    session.queue(_FakeResponse(503), _FakeResponse(500), _FakeResponse(200))
    client = _client(session)
    client.upload("k", b"x", content_type="application/pdf")  # must not raise
    assert len(session.calls) == 3


def test_upload_raises_after_exhausting_retries():
    from services.object_storage import ObjectStorageError

    session = _FakeSession()
    session.queue(_FakeResponse(503), _FakeResponse(503), _FakeResponse(503))
    client = _client(session)
    with pytest.raises(ObjectStorageError):
        client.upload("k", b"x", content_type="application/pdf")


def test_download_missing_object_raises():
    from services.object_storage import ObjectStorageError

    session = _FakeSession()
    session.queue(_FakeResponse(404, text="Not found"))
    client = _client(session)
    with pytest.raises(ObjectStorageError):
        client.download("missing/key")


# ---------------------------------------------------------------------------
# Durability wiring: producer stores to object storage, consumer restores
# from it. Uses a fake in-memory ObjectStorage injected into the handlers.
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload(self, key, content, *, content_type="application/octet-stream"):
        self.objects[key] = bytes(content)

    def download(self, key):
        from services.object_storage import ObjectStorageError

        if key not in self.objects:
            raise ObjectStorageError(f"no such object {key}")
        return self.objects[key]

    def exists(self, key):
        return key in self.objects


def test_producer_stores_bytes_in_object_store_without_base64_in_payload(tmp_path):
    from workers.inbox_handlers import store_attachments_durably

    content = b"%PDF-1.4 durable booking"
    sha = hashlib.sha256(content).hexdigest()
    path = tmp_path / "msg_0_booking.pdf"
    path.write_bytes(content)

    pre_saved = [{"file_path": str(path), "content_sha256": sha, "filename": "booking.pdf"}]
    message = {"attachments": [{"content": content, "filename": "booking.pdf"}]}

    store = _FakeStore()
    result = store_attachments_durably(pre_saved, message, storage=store)

    assert result[0].get("storage_key")  # a durable object reference was recorded
    assert "_content_b64" not in result[0]  # base64 NOT kept in the payload
    assert store.objects[result[0]["storage_key"]] == content  # bytes are durable


def test_consumer_restores_missing_file_from_object_store(tmp_path):
    from workers.inbox_handlers import _rehydrate_attachment_files, store_attachments_durably

    content = b"%PDF-1.4 durable booking"
    sha = hashlib.sha256(content).hexdigest()
    path = tmp_path / "msg_0_booking.pdf"
    path.write_bytes(content)
    pre_saved = [{"file_path": str(path), "content_sha256": sha, "filename": "booking.pdf"}]
    message = {"attachments": [{"content": content, "filename": "booking.pdf"}]}

    store = _FakeStore()
    stored = store_attachments_durably(pre_saved, message, storage=store)

    # Fresh GitHub-Actions runner: the local file is gone.
    path.unlink()
    assert not path.exists()

    restored = _rehydrate_attachment_files(stored, storage=store)
    assert restored == 1
    assert path.read_bytes() == content  # downloaded + verified from object storage


def test_consumer_fails_closed_on_object_store_sha_mismatch(tmp_path):
    from workers.inbox_handlers import AttachmentDurabilityError, _rehydrate_attachment_files

    store = _FakeStore()
    store.objects["operations_inbox/k"] = b"tampered bytes"
    pre_saved = [
        {
            "file_path": str(tmp_path / "gone.pdf"),
            "content_sha256": hashlib.sha256(b"original").hexdigest(),
            "storage_key": "operations_inbox/k",
        }
    ]
    with pytest.raises(AttachmentDurabilityError):
        _rehydrate_attachment_files(pre_saved, storage=store)


def test_consumer_fails_closed_when_object_missing_and_no_base64(tmp_path):
    from workers.inbox_handlers import AttachmentDurabilityError, _rehydrate_attachment_files

    store = _FakeStore()  # empty - object was never uploaded / evicted
    pre_saved = [
        {
            "file_path": str(tmp_path / "gone.pdf"),
            "content_sha256": hashlib.sha256(b"x").hexdigest(),
            "storage_key": "operations_inbox/missing",
        }
    ]
    with pytest.raises(AttachmentDurabilityError):
        _rehydrate_attachment_files(pre_saved, storage=store)


def test_producer_falls_back_to_base64_when_storage_unconfigured(tmp_path):
    """No object storage configured -> keep the Stage 1 base64-in-payload
    durability so the system still works with zero external infrastructure."""
    from workers.inbox_handlers import store_attachments_durably

    content = b"%PDF-1.4 fallback"
    sha = hashlib.sha256(content).hexdigest()
    pre_saved = [{"file_path": str(tmp_path / "f.pdf"), "content_sha256": sha}]
    message = {"attachments": [{"content": content, "filename": "f.pdf"}]}

    result = store_attachments_durably(pre_saved, message, storage=None)
    assert result[0]["_content_b64"] == base64.b64encode(content).decode("ascii")
    assert "storage_key" not in result[0]


def test_oversized_attachment_is_skipped_from_object_store(tmp_path):
    """An attachment over the durable-store per-file cap is not uploaded (and
    not base64-embedded); it stays same-run-only, recorded explicitly."""
    from workers import inbox_handlers
    from workers.inbox_handlers import store_attachments_durably

    big = b"x" * (inbox_handlers._ATTACHMENT_STORE_PER_FILE_CAP + 1)
    sha = hashlib.sha256(big).hexdigest()
    pre_saved = [{"file_path": str(tmp_path / "big.bin"), "content_sha256": sha}]
    message = {"attachments": [{"content": big, "filename": "big.bin"}]}

    store = _FakeStore()
    result = store_attachments_durably(pre_saved, message, storage=store)
    assert "storage_key" not in result[0]
    assert "_content_b64" not in result[0]
    assert result[0].get("durable") is False  # explicitly flagged non-durable
    assert store.objects == {}
