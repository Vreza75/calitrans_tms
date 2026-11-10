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


def test_process_message_marks_backfill_rows_historical_only_when_flagged():
    import workers.inbox_handlers as h

    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"conversation_key": ""},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status"), mock.patch(
        "services.operations_inbox_service.mark_intake_historical_backfill"
    ) as mark, mock.patch(
        "services.operations_inbox_service._email_sync_unique_message_id", return_value="mid-1"
    ):
        h.handle_inbox_process_message({"message": {"subject": "x"}, "backfill": True})
        mark.assert_called_once_with("mid-1")

        mark.reset_mock()
        h.handle_inbox_process_message({"message": {"subject": "x"}})  # live, no flag
        mark.assert_not_called()


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
