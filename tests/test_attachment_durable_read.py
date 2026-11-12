"""PR #19 Stage 2 / Task A blocker 2: durable read lifecycle. Preview / download
/ reparse must resolve persisted object references, fetch bytes from the private
store, and verify SHA-256 - while authorization stays at the application
boundary and internal references never leak to a client.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest


class _FakeStore:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def download(self, key):
        from services.object_storage import ObjectStorageError

        if key not in self.objects:
            raise ObjectStorageError(f"missing {key}")
        return self.objects[key]


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------

def test_resolver_prefers_verified_local_file(tmp_path):
    from services.operations_attachment_core import resolve_attachment_bytes

    content = b"%PDF local"
    sha = hashlib.sha256(content).hexdigest()
    path = tmp_path / "a.pdf"
    path.write_bytes(content)
    out = resolve_attachment_bytes(str(path), storage_key="k", content_sha256=sha, storage=_FakeStore())
    assert out == content  # served from disk, store untouched


def test_resolver_falls_back_to_object_store_when_file_gone(tmp_path):
    from services.operations_attachment_core import resolve_attachment_bytes

    content = b"%PDF durable"
    sha = hashlib.sha256(content).hexdigest()
    store = _FakeStore({"operations_inbox/ab/x": content})
    out = resolve_attachment_bytes(
        str(tmp_path / "gone.pdf"), storage_key="operations_inbox/ab/x", content_sha256=sha, storage=store
    )
    assert out == content


def test_resolver_uses_store_when_disk_file_corrupted(tmp_path):
    from services.operations_attachment_core import resolve_attachment_bytes

    good = b"%PDF good"
    sha = hashlib.sha256(good).hexdigest()
    path = tmp_path / "a.pdf"
    path.write_bytes(b"corrupted")
    store = _FakeStore({"k": good})
    out = resolve_attachment_bytes(str(path), storage_key="k", content_sha256=sha, storage=store)
    assert out == good


def test_resolver_raises_on_store_sha_mismatch(tmp_path):
    from services.operations_attachment_core import AttachmentResolutionError, resolve_attachment_bytes

    store = _FakeStore({"k": b"tampered"})
    with pytest.raises(AttachmentResolutionError):
        resolve_attachment_bytes(
            str(tmp_path / "gone.pdf"), storage_key="k", content_sha256=hashlib.sha256(b"real").hexdigest(), storage=store
        )


def test_resolver_raises_when_no_durable_source(tmp_path):
    from services.operations_attachment_core import AttachmentResolutionError, resolve_attachment_bytes

    with pytest.raises(AttachmentResolutionError):
        resolve_attachment_bytes(str(tmp_path / "gone.pdf"), storage_key="", content_sha256="abc", storage=_FakeStore())


def test_resolver_raises_when_object_missing(tmp_path):
    from services.operations_attachment_core import AttachmentResolutionError, resolve_attachment_bytes

    with pytest.raises(AttachmentResolutionError):
        resolve_attachment_bytes(
            str(tmp_path / "gone.pdf"), storage_key="operations_inbox/missing", content_sha256="abc", storage=_FakeStore()
        )


# ---------------------------------------------------------------------------
# API download path: later download after the worker runner is gone.
# ---------------------------------------------------------------------------

def _record_with_attachment(file_path: str, storage_key: str, sha: str) -> pd.DataFrame:
    parsed = {
        "_operations_attachments": [
            {
                "filename": "booking.pdf",
                "file_path": file_path,
                "content_type": "application/pdf",
                "is_pdf": True,
                "storage_key": storage_key,
                "content_sha256": sha,
            }
        ]
    }
    return pd.DataFrame([{"id": 42, "source_message_id": "m1", "parsed_data": json.dumps(parsed)}])


def test_api_download_resolves_from_object_store_after_runner_gone(monkeypatch, tmp_path):
    from application.work_items import queries

    content = b"%PDF durable api"
    sha = hashlib.sha256(content).hexdigest()
    store = _FakeStore({"operations_inbox/ab/thekey": content})

    monkeypatch.setattr(
        queries.inbox_repo,
        "load_operations_inbox_record",
        lambda wid: _record_with_attachment(str(tmp_path / "gone.pdf"), "operations_inbox/ab/thekey", sha),
    )
    monkeypatch.setattr("services.object_storage.get_object_storage", lambda: store)

    ref = queries.attachment_ref(str(tmp_path / "gone.pdf"))
    data, meta = queries.get_attachment_content(42, ref)
    assert data == content
    assert meta.filename == "booking.pdf"


def test_api_download_unknown_ref_is_not_found(monkeypatch, tmp_path):
    from application.work_items import queries
    from application.exceptions import NotFoundError

    monkeypatch.setattr(
        queries.inbox_repo,
        "load_operations_inbox_record",
        lambda wid: _record_with_attachment(str(tmp_path / "gone.pdf"), "operations_inbox/ab/thekey", "abc"),
    )
    with pytest.raises(NotFoundError):
        queries.get_attachment_content(42, "not-a-real-ref")


def test_api_download_corrupted_object_is_not_served(monkeypatch, tmp_path):
    from application.work_items import queries
    from application.exceptions import NotFoundError

    sha = hashlib.sha256(b"original").hexdigest()
    store = _FakeStore({"operations_inbox/ab/thekey": b"tampered"})
    monkeypatch.setattr(
        queries.inbox_repo,
        "load_operations_inbox_record",
        lambda wid: _record_with_attachment(str(tmp_path / "gone.pdf"), "operations_inbox/ab/thekey", sha),
    )
    monkeypatch.setattr("services.object_storage.get_object_storage", lambda: store)
    ref = queries.attachment_ref(str(tmp_path / "gone.pdf"))
    with pytest.raises(NotFoundError):  # SHA mismatch -> not served
        queries.get_attachment_content(42, ref)


# ---------------------------------------------------------------------------
# Internal references must never serialize to an API client.
# ---------------------------------------------------------------------------

def test_api_schema_never_leaks_storage_key_or_path():
    from application.work_items.models import AttachmentMeta, AttachmentSummary
    from api.schemas.work_items import AttachmentSummaryOut

    meta = AttachmentMeta(
        filename="b.pdf",
        file_path="/srv/secret/path/b.pdf",
        content_type="application/pdf",
        is_pdf=True,
        attachment_ref="ref123",
        storage_key="operations_inbox/ab/secretkey",
        content_sha256="deadbeef",
    )
    out = AttachmentSummaryOut.from_domain(AttachmentSummary(current=[meta], prior=[]))
    dumped = out.model_dump_json()
    assert "secretkey" not in dumped
    assert "/srv/secret/path" not in dumped
    assert "deadbeef" not in dumped
    assert "ref123" in dumped  # the opaque ref is what the client gets
