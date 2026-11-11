"""PR #19 hardening: attachment durability across runners, backfill
isolation, and backfill progress (new vs idempotency-conflict). Everything
DB/IMAP is mocked - these assert the control logic, not real persistence."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from unittest import mock


# --------------------------------------------------------------------------
# P1 - Attachment durability across separate runner executions
# --------------------------------------------------------------------------

def test_embed_then_rehydrate_restores_a_missing_attachment_file(tmp_path):
    from workers.inbox_handlers import _embed_attachment_content, _rehydrate_attachment_files

    content = b"%PDF-1.4 fake booking pdf bytes"
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    file_path = tmp_path / "msg_0_booking.pdf"
    file_path.write_bytes(content)

    pre_saved = [{"file_path": str(file_path), "content_sha256": sha, "filename": "booking.pdf"}]
    message = {"attachments": [{"content": content, "filename": "booking.pdf"}]}

    embedded = _embed_attachment_content(pre_saved, message)
    assert embedded[0]["_content_b64"] == base64.b64encode(content).decode("ascii")

    # Simulate a fresh GitHub-Actions runner: the saved file is gone.
    file_path.unlink()
    assert not file_path.exists()

    restored = _rehydrate_attachment_files(embedded)
    assert restored == 1
    assert file_path.read_bytes() == content  # cross-worker availability


def test_embed_skips_oversized_attachment(tmp_path):
    from workers import inbox_handlers

    big = b"x" * (inbox_handlers._ATTACHMENT_EMBED_PER_FILE_CAP + 1)
    import hashlib

    sha = hashlib.sha256(big).hexdigest()
    pre_saved = [{"file_path": str(tmp_path / "big.bin"), "content_sha256": sha}]
    message = {"attachments": [{"content": big}]}
    embedded = inbox_handlers._embed_attachment_content(pre_saved, message)
    assert "_content_b64" not in embedded[0]  # too big -> stays same-run-only


def test_rehydrate_does_not_overwrite_an_existing_file(tmp_path):
    from workers.inbox_handlers import _rehydrate_attachment_files

    path = tmp_path / "present.pdf"
    path.write_bytes(b"original")
    pre_saved = [{"file_path": str(path), "_content_b64": base64.b64encode(b"other").decode("ascii")}]
    assert _rehydrate_attachment_files(pre_saved) == 0
    assert path.read_bytes() == b"original"


# --------------------------------------------------------------------------
# P1 - Backfill isolation: historical mail never becomes active work
# --------------------------------------------------------------------------

def test_backfill_row_status_is_terminal_so_active_queue_excludes_it():
    from services.operations_inbox_service import (
        HISTORICAL_BACKFILL_REVIEW_STATUS,
        INBOX_TERMINAL_REVIEW_STATUSES,
    )

    assert HISTORICAL_BACKFILL_REVIEW_STATUS in INBOX_TERMINAL_REVIEW_STATUSES


def test_process_message_inserts_backfill_rows_with_atomic_historical_status():
    import workers.inbox_handlers as h
    from services.operations_inbox_service import HISTORICAL_BACKFILL_REVIEW_STATUS

    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"conversation_key": ""},
    ) as insert, mock.patch("services.operations_inbox_service.sync_conversation_status"):
        h.handle_inbox_process_message({"message": {"subject": "x"}, "backfill": True})
        assert insert.call_args.kwargs["force_review_status"] == HISTORICAL_BACKFILL_REVIEW_STATUS

        insert.reset_mock()
        h.handle_inbox_process_message({"message": {"subject": "x"}})  # live, no flag
        assert insert.call_args.kwargs["force_review_status"] is None


def test_rehydrate_fails_closed_when_missing_and_no_durable_copy(tmp_path):
    from workers.inbox_handlers import AttachmentDurabilityError, _rehydrate_attachment_files

    import hashlib

    pre_saved = [
        {"file_path": str(tmp_path / "gone.pdf"), "content_sha256": hashlib.sha256(b"x").hexdigest()}
    ]  # no _content_b64, file never created
    import pytest

    with pytest.raises(AttachmentDurabilityError):
        _rehydrate_attachment_files(pre_saved)


def test_rehydrate_fails_closed_on_sha_mismatch(tmp_path):
    import base64

    import pytest

    from workers.inbox_handlers import AttachmentDurabilityError, _rehydrate_attachment_files

    pre_saved = [
        {
            "file_path": str(tmp_path / "gone.pdf"),
            "content_sha256": "deadbeef",  # won't match the embedded bytes
            "_content_b64": base64.b64encode(b"real bytes").decode("ascii"),
        }
    ]
    with pytest.raises(AttachmentDurabilityError):
        _rehydrate_attachment_files(pre_saved)


def test_rehydrate_restores_corrupted_on_disk_file_from_durable_copy(tmp_path):
    import base64
    import hashlib

    from workers.inbox_handlers import _rehydrate_attachment_files

    good = b"%PDF good bytes"
    sha = hashlib.sha256(good).hexdigest()
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"corrupted")  # on-disk copy is wrong
    pre_saved = [
        {"file_path": str(path), "content_sha256": sha, "_content_b64": base64.b64encode(good).decode("ascii")}
    ]
    assert _rehydrate_attachment_files(pre_saved) == 1
    assert path.read_bytes() == good


# --------------------------------------------------------------------------
# P1 - Backfill progress: new vs idempotency-conflict, bounded + resumable
# --------------------------------------------------------------------------

@contextmanager
def _fake_txn():
    yield object()


def _run_backfill_with(messages, *, already_imported, job_exists, batch):
    from datetime import date

    import db_client
    import scripts.backfill_inbox as bf
    from repositories import worker_job_repo

    enqueued = {"n": 0}
    with mock.patch("services.email_client._operations_email_accounts", return_value=[{"email": "dispatch@x.com", "password": "p"}]), mock.patch(
        "services.email_client._fetch_recent_emails", return_value=messages
    ), mock.patch("services.operations_inbox_service.ensure_operations_email_sync_schema"), mock.patch(
        "services.operations_inbox_service.load_existing_operations_email_lookup", return_value={"by_message_id": {}}
    ), mock.patch(
        "services.operations_inbox_service.operations_email_already_imported", side_effect=already_imported
    ), mock.patch(
        "services.operations_inbox_service._save_operations_email_attachments", return_value=[]
    ), mock.patch("services.operations_inbox_service._email_sync_unique_message_id", side_effect=lambda m: m["id"]), mock.patch(
        "db_client.transaction", _fake_txn
    ), mock.patch.object(worker_job_repo, "job_exists_for_idempotency_key", side_effect=job_exists), mock.patch.object(
        worker_job_repo, "enqueue_job", side_effect=lambda **kw: enqueued.update(n=enqueued["n"] + 1)
    ):
        totals = bf.run_backfill(since=date(2026, 1, 1), until=date(2026, 1, 31), batch=batch, dry_run=False)
    return totals, enqueued["n"]


def test_backfill_distinguishes_new_from_conflict_and_skips_imported():
    msgs = [{"id": "a", "subject": "s"}, {"id": "b", "subject": "s"}, {"id": "c", "subject": "s"}]
    # a already imported (skip); b new (queue); c already has a job (already_queued).
    imported = {"a": True, "b": False, "c": False}
    exists = {"a": False, "b": False, "c": True}
    totals, enqueued = _run_backfill_with(
        msgs, already_imported=lambda mid, **k: imported[mid], job_exists=lambda conn, key: exists[key.split(":")[-1]], batch=50
    )
    assert totals["skipped"] == 1
    assert totals["queued"] == 1
    assert totals["already_queued"] == 1
    assert enqueued == 1  # only the genuinely new one was enqueued


def test_backfill_is_bounded_by_batch_over_a_large_window():
    msgs = [{"id": str(i), "subject": "s"} for i in range(500)]  # large window
    totals, enqueued = _run_backfill_with(
        msgs, already_imported=lambda mid, **k: False, job_exists=lambda conn, key: False, batch=100
    )
    assert totals["queued"] == 100  # bounded per run
    assert enqueued == 100


def test_backfill_second_run_after_interruption_enqueues_nothing_new():
    msgs = [{"id": "a", "subject": "s"}, {"id": "b", "subject": "s"}]
    # Simulate a resumed run: both already have jobs from the interrupted run.
    totals, enqueued = _run_backfill_with(
        msgs, already_imported=lambda mid, **k: False, job_exists=lambda conn, key: True, batch=50
    )
    assert totals["queued"] == 0
    assert totals["already_queued"] == 2
    assert enqueued == 0  # idempotent, no duplicate work


# --------------------------------------------------------------------------
# P2 - Logs carry no customer info or credentials
# --------------------------------------------------------------------------

def test_per_mailbox_sync_log_has_no_address_subject_or_password(caplog):
    import logging

    import workers.inbox_handlers as h

    diagnostics = {
        "per_account": [
            {"email": "dispatch@calitranscorp.com", "login_success": True, "messages_found": 4, "error_type": ""}
        ]
    }
    per_mailbox = {"DISPATCH": {"skipped": 1, "queued": 3}}
    with caplog.at_level(logging.INFO, logger=h.logger.name):
        h._log_per_mailbox_sync(diagnostics, per_mailbox)

    text = caplog.text
    assert "mailbox=DISPATCH" in text
    assert "@" not in text  # no address
    assert "calitranscorp.com" not in text  # no domain
    assert "jobs_queued=3" in text and "skipped_already_imported=1" in text


# --------------------------------------------------------------------------
# Stage 1 #2 - ONE canonical terminal-status filter across every active queue
# (Streamlit inbox, FastAPI work-item queue, dispatcher workspace). A second
# divergent copy is exactly how backfilled mail leaked into a live queue.
# --------------------------------------------------------------------------

def test_terminal_status_filter_is_single_canonical_source_across_layers():
    from repositories import inbox_repo
    from services import operations_inbox_service as svc

    # Re-exported, not re-defined: the service shares the repository's exact list
    # object and where-clause function, so the two can never drift apart.
    assert svc.INBOX_TERMINAL_REVIEW_STATUSES is inbox_repo.INBOX_TERMINAL_REVIEW_STATUSES
    assert svc.inbox_review_where_clause is inbox_repo.inbox_review_where_clause
    assert svc.HISTORICAL_BACKFILL_REVIEW_STATUS == inbox_repo.HISTORICAL_BACKFILL_REVIEW_STATUS


def test_active_queue_clause_excludes_historical_rows_behaviorally():
    """Behavioral: run the real inbox_review_where_clause() SQL against a
    disposable in-memory SQLite table and confirm historical/terminal rows are
    filtered out while live rows (including NULL->'Open') survive. This is the
    exact clause every active-queue surface (Streamlit, FastAPI work-item queue
    via work_item_repo, dispatcher workspace) runs."""
    import sqlite3

    from repositories.inbox_repo import (
        HISTORICAL_BACKFILL_REVIEW_STATUS,
        inbox_review_where_clause,
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("create table order_intake (id integer primary key, review_status text)")
        conn.executemany(
            "insert into order_intake (id, review_status) values (?, ?)",
            [
                (1, "Open"),  # active
                (2, None),  # coalesced to 'Open' -> active
                (3, "Order Created"),  # terminal -> excluded
                (4, HISTORICAL_BACKFILL_REVIEW_STATUS),  # the regression: must be excluded
            ],
        )
        rows = conn.execute(
            f"select id from order_intake {inbox_review_where_clause()} order by id"
        ).fetchall()
    finally:
        conn.close()

    assert [r[0] for r in rows] == [1, 2]  # historical + order-created filtered out


# --------------------------------------------------------------------------
# Stage 1 #3 - "Create Load From Document" is gated by the learning-phase flag
# (email-originated create), while manual create_load_from_intake stays ungated.
# --------------------------------------------------------------------------

def test_create_load_from_document_refuses_when_learning_phase_disabled(monkeypatch):
    """Behavioral: the 'Create Load From Document' button delegates to
    create_load_from_document_fields. With the learning-phase flag OFF it must
    refuse (PermissionError) WITHOUT ever reaching the create primitive; with it
    ON it creates, merging document fields over parsed fields and dropping
    underscore-prefixed internals."""
    import pytest

    from services import operations_inbox_service as svc

    created: list = []
    monkeypatch.setattr(
        svc, "create_load_from_intake", lambda intake_id, merged: created.append((intake_id, merged)) or 999
    )

    monkeypatch.setattr(svc, "email_load_creation_enabled", lambda: False)
    with pytest.raises(PermissionError):
        svc.create_load_from_document_fields(7, {"Customer": "X"}, {"Booking Number": "B1"})
    assert created == []  # gate fired before the primitive

    monkeypatch.setattr(svc, "email_load_creation_enabled", lambda: True)
    load_id = svc.create_load_from_document_fields(
        7, {"Customer": "X", "Booking Number": "OLD"}, {"Booking Number": "B1", "_internal": "skip"}
    )
    assert load_id == 999
    intake_id, merged = created[0]
    assert intake_id == 7
    assert merged["Booking Number"] == "B1"  # document field overrides parsed
    assert merged["Customer"] == "X"
    assert "_internal" not in merged  # underscore-prefixed internals dropped


def test_manual_create_load_from_intake_is_not_gated_behaviorally(monkeypatch):
    """Behavioral companion: the shared manual primitive create_load_from_intake
    creates WITHOUT consulting the email learning-phase flag, so legitimate
    non-email load entry keeps working even while email creation is disabled."""
    from services import order_intake

    captured: list = []
    monkeypatch.setattr(
        order_intake,
        "create_load_from_intake_with_status",
        lambda record_id, edited_data, *, conn=None: (captured.append(record_id) or (555, True)),
    )
    # Email flag explicitly OFF - manual path must still succeed.
    monkeypatch.setenv("EMAIL_LOAD_CREATION_ENABLED", "false")
    load_id = order_intake.create_load_from_intake(42, {"Customer": "Acme"})
    assert load_id == 555
    assert captured == [42]


# --------------------------------------------------------------------------
# Stage 1 #4 - historical backfill never changes LIVE conversation status.
# --------------------------------------------------------------------------

def test_backfill_does_not_reconcile_live_conversation_status():
    import workers.inbox_handlers as h

    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"conversation_key": "conv-1"},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status") as sync:
        h.handle_inbox_process_message({"message": {"subject": "x"}, "backfill": True})
        sync.assert_not_called()  # historical mail must not touch live status

        sync.reset_mock()
        h.handle_inbox_process_message({"message": {"subject": "x"}})  # live sync
        sync.assert_called_once_with("conv-1")
