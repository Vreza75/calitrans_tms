"""PR #19 Stage 2 / Task A blocker 4: attachment base64 bodies must never
persist in order_intake.parsed_data or in a completed worker_jobs payload, and
legacy rows that already carry them must be safely scrubbable.
"""
from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import text

import db_client
from repositories import worker_job_repo


# ---------------------------------------------------------------------------
# strip_attachment_runtime_bytes - the persistence-time sanitizer
# ---------------------------------------------------------------------------

def test_strip_removes_bytes_keeps_references():
    from services.operations_attachment_core import strip_attachment_runtime_bytes

    items = [
        {
            "filename": "b.pdf",
            "file_path": "/x/b.pdf",
            "content_sha256": "abc",
            "storage_key": "operations_inbox/ab/abc",
            "size_bytes": 10,
            "_content_b64": base64.b64encode(b"bytes").decode("ascii"),
            "content": b"rawbytes",
        }
    ]
    cleaned = strip_attachment_runtime_bytes(items)
    assert "_content_b64" not in cleaned[0]
    assert "content" not in cleaned[0]
    # References/checksums preserved so the durable copy stays resolvable.
    assert cleaned[0]["storage_key"] == "operations_inbox/ab/abc"
    assert cleaned[0]["content_sha256"] == "abc"
    assert cleaned[0]["file_path"] == "/x/b.pdf"


def test_strip_is_idempotent_and_safe_on_junk():
    from services.operations_attachment_core import strip_attachment_runtime_bytes

    assert strip_attachment_runtime_bytes(None) == []
    assert strip_attachment_runtime_bytes([{"a": 1}, "notadict"]) == [{"a": 1}]


# ---------------------------------------------------------------------------
# worker_jobs payload scrub - only completed jobs, never active retries.
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_worker_jobs(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'wj.db'}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)
    with db_client.get_engine(url).begin() as conn:
        conn.execute(
            text(
                "create table worker_jobs (id integer primary key autoincrement, job_type text, "
                "aggregate_type text, aggregate_id text, payload text not null default '{}', "
                "idempotency_key text unique, status text not null default 'pending', "
                "attempt_count integer default 0, available_at timestamp, created_at timestamp, "
                "claimed_at timestamp, completed_at timestamp, last_error text, actor text)"
            )
        )
    yield url
    db_client._ENGINE_CACHE.pop(url, None)


def _insert_job(status: str, payload: dict) -> int:
    with db_client.transaction() as conn:
        row = conn.execute(
            text(
                "insert into worker_jobs (job_type, aggregate_type, aggregate_id, payload, idempotency_key, status) "
                "values ('inbox.process_message','email_message','m', :p, :k, :s) returning id"
            ),
            {"p": json.dumps(payload), "k": f"k-{status}-{payload.get('n')}", "s": status},
        ).scalar_one()
    return int(row)


def _payload(job_id: int) -> dict:
    df = db_client.read_df("select payload from worker_jobs where id = :id", {"id": job_id})
    return json.loads(df.iloc[0]["payload"])


def _b64_payload(n=0) -> dict:
    return {
        "n": n,
        "message": {"subject": "x"},
        "pre_saved_attachments": [
            {"file_path": "/x/a.pdf", "content_sha256": "abc", "_content_b64": base64.b64encode(b"z").decode("ascii")}
        ],
    }


def test_scrub_removes_base64_from_completed_job(sqlite_worker_jobs):
    job_id = _insert_job("completed", _b64_payload())
    with db_client.transaction() as conn:
        assert worker_job_repo.scrub_completed_payload_bytes(conn, job_id) is True
    after = _payload(job_id)
    assert "_content_b64" not in after["pre_saved_attachments"][0]
    assert after["pre_saved_attachments"][0]["content_sha256"] == "abc"  # reference kept


def test_scrub_does_not_touch_pending_job(sqlite_worker_jobs):
    """An in-flight retry may still need the base64 fallback - never scrub a
    non-completed job."""
    job_id = _insert_job("pending", _b64_payload())
    with db_client.transaction() as conn:
        assert worker_job_repo.scrub_completed_payload_bytes(conn, job_id) is False
    assert "_content_b64" in _payload(job_id)["pre_saved_attachments"][0]


def test_scrub_is_idempotent_noop_when_clean(sqlite_worker_jobs):
    job_id = _insert_job("completed", {"n": 1, "pre_saved_attachments": [{"content_sha256": "abc"}]})
    with db_client.transaction() as conn:
        assert worker_job_repo.scrub_completed_payload_bytes(conn, job_id) is False


# ---------------------------------------------------------------------------
# Legacy cleanup script - scrubs completed jobs + parsed_data, skips active.
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_jobs_and_intake(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)
    with db_client.get_engine(url).begin() as conn:
        conn.execute(
            text(
                "create table worker_jobs (id integer primary key autoincrement, payload text, status text)"
            )
        )
        conn.execute(text("create table order_intake (id integer primary key autoincrement, parsed_data text)"))
    yield url
    db_client._ENGINE_CACHE.pop(url, None)


def test_legacy_script_scrubs_completed_jobs_and_parsed_data_not_active(sqlite_jobs_and_intake):
    import scripts.scrub_attachment_base64 as scrub

    b64 = base64.b64encode(b"legacy").decode("ascii")
    with db_client.transaction() as conn:
        conn.execute(
            text("insert into worker_jobs (payload, status) values (:p, 'completed')"),
            {"p": json.dumps({"pre_saved_attachments": [{"content_sha256": "a", "_content_b64": b64}]})},
        )
        conn.execute(
            text("insert into worker_jobs (payload, status) values (:p, 'processing')"),
            {"p": json.dumps({"pre_saved_attachments": [{"content_sha256": "b", "_content_b64": b64}]})},
        )
        conn.execute(
            text("insert into order_intake (parsed_data) values (:p)"),
            {"p": json.dumps({"_operations_attachments": [{"content_sha256": "c", "_content_b64": b64}]})},
        )

    totals = scrub.scrub(batch=100, dry_run=False)
    assert totals["jobs_scrubbed"] == 1  # only the completed one
    assert totals["intake_scrubbed"] == 1

    # Completed job scrubbed; processing (active) job left intact.
    done = db_client.read_df("select payload from worker_jobs where status='completed'").iloc[0]["payload"]
    active = db_client.read_df("select payload from worker_jobs where status='processing'").iloc[0]["payload"]
    assert "_content_b64" not in done
    assert "_content_b64" in active

    intake = db_client.read_df("select parsed_data from order_intake").iloc[0]["parsed_data"]
    assert "_content_b64" not in intake


def test_legacy_script_dry_run_writes_nothing(sqlite_jobs_and_intake):
    import scripts.scrub_attachment_base64 as scrub

    b64 = base64.b64encode(b"legacy").decode("ascii")
    with db_client.transaction() as conn:
        conn.execute(
            text("insert into worker_jobs (payload, status) values (:p, 'completed')"),
            {"p": json.dumps({"pre_saved_attachments": [{"content_sha256": "a", "_content_b64": b64}]})},
        )

    totals = scrub.scrub(batch=100, dry_run=True)
    assert totals["jobs_scrubbed"] == 1  # counted
    still = db_client.read_df("select payload from worker_jobs").iloc[0]["payload"]
    assert "_content_b64" in still  # but not written
