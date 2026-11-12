"""PR #19 Stage 2 / Task A blocker 5: tenant/account-scoped keys, private-bucket
verification, and reference-aware deletion.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

import db_client


# ---------------------------------------------------------------------------
# Tenant/account-scoped object keys
# ---------------------------------------------------------------------------

def test_object_key_is_tenant_and_account_scoped_and_content_addressed():
    from workers.inbox_handlers import _attachment_object_key

    sha = "a" * 64
    key = _attachment_object_key(sha, tenant="calitrans", account="tok123")
    assert key == f"inbox/calitrans/tok123/aa/{sha}"


def test_account_token_isolates_accounts_without_leaking_address():
    from workers.inbox_handlers import _account_token

    a = _account_token("dispatch@calitranscorp.com")
    b = _account_token("accounting@calitranscorp.com")
    assert a != b  # different accounts -> different key namespace
    assert "@" not in a and "calitranscorp" not in a  # address not embedded
    assert _account_token("") == "shared"  # stable empty-account bucket


def test_producer_uses_account_scoped_key(tmp_path):
    from workers.inbox_handlers import _account_token, store_attachments_durably

    class _FakeStore:
        def __init__(self):
            self.objects = {}

        def upload(self, key, content, *, content_type="application/octet-stream"):
            self.objects[key] = content

    content = b"%PDF x"
    sha = hashlib.sha256(content).hexdigest()
    pre_saved = [{"file_path": str(tmp_path / "a.pdf"), "content_sha256": sha, "filename": "a.pdf"}]
    message = {"attachments": [{"content": content, "filename": "a.pdf"}], "mailbox_account": "dispatch@x.com"}

    store = _FakeStore()
    result = store_attachments_durably(pre_saved, message, storage=store)
    assert _account_token("dispatch@x.com") in result[0]["storage_key"]


# ---------------------------------------------------------------------------
# Private-bucket verification
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content
        self.text = content.decode() if isinstance(content, bytes) else content


class _Session:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return self._resp


def _client(resp):
    from services.object_storage import SupabaseObjectStorage

    return SupabaseObjectStorage(
        base_url="https://proj.supabase.co", bucket="b", token="t", session=_Session(resp)
    )


def test_bucket_is_private_true_for_private_bucket():
    client = _client(_Resp(200, json.dumps({"public": False}).encode()))
    assert client.bucket_is_private() is True


def test_bucket_is_private_false_for_public_bucket():
    client = _client(_Resp(200, json.dumps({"public": True}).encode()))
    assert client.bucket_is_private() is False


def test_assert_bucket_private_raises_on_public():
    from services.object_storage import ObjectStorageError, assert_bucket_private

    client = _client(_Resp(200, json.dumps({"public": True}).encode()))
    with pytest.raises(ObjectStorageError):
        assert_bucket_private(client)


def test_assert_bucket_private_noop_when_unconfigured():
    from services.object_storage import assert_bucket_private

    assert assert_bucket_private(None) is None  # no storage -> no-op


# ---------------------------------------------------------------------------
# Reference-aware deletion
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_refs(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'refs.db'}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)
    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table order_intake (id integer primary key autoincrement, parsed_data text)"))
        conn.execute(
            text("create table worker_jobs (id integer primary key autoincrement, payload text, status text)")
        )
    yield url
    db_client._ENGINE_CACHE.pop(url, None)


class _DeletableStore:
    def __init__(self):
        self.deleted = []

    def delete(self, key):
        self.deleted.append(key)


def test_object_referenced_by_order_intake_is_not_purged(sqlite_refs):
    from services.attachment_retention import purge_object_if_unreferenced

    key = "inbox/calitrans/tok/ab/thesha"
    with db_client.transaction() as conn:
        conn.execute(
            text("insert into order_intake (parsed_data) values (:p)"),
            {"p": json.dumps({"_operations_attachments": [{"storage_key": key}]})},
        )
    store = _DeletableStore()
    assert purge_object_if_unreferenced(key, storage=store) is False
    assert store.deleted == []


def test_object_referenced_by_active_job_is_not_purged(sqlite_refs):
    from services.attachment_retention import purge_object_if_unreferenced

    key = "inbox/calitrans/tok/ab/thesha"
    with db_client.transaction() as conn:
        conn.execute(
            text("insert into worker_jobs (payload, status) values (:p, 'pending')"),
            {"p": json.dumps({"pre_saved_attachments": [{"storage_key": key}]})},
        )
    store = _DeletableStore()
    assert purge_object_if_unreferenced(key, storage=store) is False
    assert store.deleted == []


def test_unreferenced_object_is_purged(sqlite_refs):
    from services.attachment_retention import purge_object_if_unreferenced

    key = "inbox/calitrans/tok/ab/orphan"
    # A completed job referencing it does NOT keep it alive (terminal).
    with db_client.transaction() as conn:
        conn.execute(
            text("insert into worker_jobs (payload, status) values (:p, 'completed')"),
            {"p": json.dumps({"pre_saved_attachments": [{"storage_key": key}]})},
        )
    store = _DeletableStore()
    assert purge_object_if_unreferenced(key, storage=store) is True
    assert store.deleted == [key]


def test_blank_key_is_never_purged(sqlite_refs):
    from services.attachment_retention import object_is_referenced, purge_object_if_unreferenced

    assert object_is_referenced("") is True
    assert purge_object_if_unreferenced("", storage=_DeletableStore()) is False
