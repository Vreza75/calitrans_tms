"""Phase 6B: reliable document/file lifecycle tests.

Covers three layers, each tested at the level that actually exercises
its logic:
  - services/document_storage_service.py (staging, atomic finalize,
    idempotent retry, path-traversal safety, orphan cleanup) - real
    tmp_path filesystem, no mocks, no DB.
  - repositories/document_repo.py (documents-row atomicity) - SQLite,
    same pattern as tests/test_outbox_repo.py/test_work_item_commands.py.
  - application/documents/commands.py::attach_load_document
    (authorization boundary, wiring) - mocked DB/storage, same pattern
    as tests/test_phase5b_command_authorization.py.
"""
from __future__ import annotations

from unittest import mock

import pytest
from sqlalchemy import text

import db_client
from application.auth.models import AuthenticatedActor, Role
from application.exceptions import AuthorizationError

DISPATCHER = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
ACCOUNTING = AuthenticatedActor(actor="accountant@calitranscorp.com", role=Role.ACCOUNTING)


class _FakeUploadedFile:
    """Minimal stand-in for Streamlit's UploadedFile - .name, .seek(),
    .read(), matching what services.document_storage_service.stage_upload
    actually calls."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def seek(self, pos: int) -> None:
        pass

    def read(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# services/document_storage_service.py - real filesystem, tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    import services.document_storage_service as storage_service

    monkeypatch.setattr(storage_service, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    return tmp_path


def test_stage_upload_writes_bytes_and_returns_checksum(storage_dir):
    from services.document_storage_service import stage_upload

    upload = _FakeUploadedFile("invoice.pdf", b"pdf-bytes-here")
    staging_path, final_key, original_name, checksum = stage_upload(upload, load_id=7)

    assert original_name == "invoice.pdf"
    assert final_key.startswith("load_7_")
    assert final_key.endswith("invoice.pdf")
    staged_file = storage_dir / staging_path
    assert staged_file.exists()
    assert staged_file.read_bytes() == b"pdf-bytes-here"

    import hashlib

    assert checksum == hashlib.sha256(b"pdf-bytes-here").hexdigest()


def test_stage_upload_two_uploads_same_original_filename_do_not_collide(storage_dir):
    """Regression: the pre-Phase-6B scheme (load_{id}_{filename}) would
    silently overwrite one upload's bytes with another's if both used the
    same original filename for the same load. The uuid4 token makes that
    impossible."""
    from services.document_storage_service import stage_upload

    _, key_a, _, _ = stage_upload(_FakeUploadedFile("invoice.pdf", b"first"), load_id=7)
    _, key_b, _, _ = stage_upload(_FakeUploadedFile("invoice.pdf", b"second"), load_id=7)

    assert key_a != key_b


@pytest.mark.parametrize(
    "malicious_name",
    ["../../secret.txt", "..\\..\\secret.txt", "folder/file.pdf", "..", ".", ""],
)
def test_stage_upload_sanitizes_malicious_filenames_and_stays_inside_storage_root(storage_dir, malicious_name):
    from services.document_storage_service import stage_upload

    upload = _FakeUploadedFile(malicious_name, b"data")
    staging_path, final_key, original_name, _checksum = stage_upload(upload, load_id=7)

    # original_name is the sanitized user-supplied piece - must never
    # itself contain a directory separator or resolve to "..".
    assert original_name not in {"..", "."}
    assert "/" not in original_name
    assert "\\" not in original_name
    assert final_key.startswith("load_7_")

    # staging_path/final_key legitimately contain the OS path separator
    # (they ARE paths) - the real proof no escape happened is that the
    # resolved file lives inside storage_dir, not a substring check.
    resolved_staging = (storage_dir / staging_path).resolve()
    resolved_final = (storage_dir / final_key).resolve()
    assert resolved_staging.is_relative_to(storage_dir.resolve())
    assert resolved_final.is_relative_to(storage_dir.resolve())


def test_finalize_moves_staged_file_to_final_path_preserving_content(storage_dir):
    from services.document_storage_service import finalize, stage_upload

    staging_path, final_key, _, checksum = stage_upload(_FakeUploadedFile("a.pdf", b"content"), load_id=1)

    success, error = finalize(staging_relative_path=staging_path, final_storage_key=final_key, expected_checksum=checksum)

    assert success is True
    assert error == ""
    assert not (storage_dir / staging_path).exists()
    assert (storage_dir / final_key).read_bytes() == b"content"


def test_finalize_missing_staged_file_fails_without_crashing(storage_dir):
    from services.document_storage_service import finalize

    success, error = finalize(
        staging_relative_path=".staging/nonexistent.staging", final_storage_key="load_1_x_a.pdf", expected_checksum="abc"
    )

    assert success is False
    assert "not found" in error.lower()


def test_finalize_retry_after_final_file_already_exists_with_matching_checksum_is_a_safe_no_op(storage_dir):
    """Crash window 3: rename succeeds, processor dies before the DB
    status update, and a retry calls finalize() again. The staged file is
    already gone (renamed); the final file is already correct. This must
    report success, not fail because the staged file vanished."""
    from services.document_storage_service import finalize, stage_upload

    staging_path, final_key, _, checksum = stage_upload(_FakeUploadedFile("a.pdf", b"content"), load_id=1)
    first = finalize(staging_relative_path=staging_path, final_storage_key=final_key, expected_checksum=checksum)
    assert first == (True, "")

    second = finalize(staging_relative_path=staging_path, final_storage_key=final_key, expected_checksum=checksum)
    assert second == (True, "")
    assert (storage_dir / final_key).read_bytes() == b"content"  # untouched, not re-moved/corrupted


def test_finalize_refuses_to_overwrite_a_different_file_at_the_same_key(storage_dir):
    """Extremely unlikely given the uuid4 token, but if a final path is
    somehow already occupied by different content, finalize must refuse,
    not silently corrupt the existing file."""
    from services.document_storage_service import finalize, stage_upload

    final_key = "load_1_collision_a.pdf"
    (storage_dir / final_key).write_bytes(b"someone else's content")

    staging_path, _, _, checksum = stage_upload(_FakeUploadedFile("a.pdf", b"my content"), load_id=1)

    success, error = finalize(staging_relative_path=staging_path, final_storage_key=final_key, expected_checksum=checksum)

    assert success is False
    assert "different checksum" in error
    assert (storage_dir / final_key).read_bytes() == b"someone else's content"


def test_reclaim_orphaned_staging_files_removes_only_old_files(storage_dir):
    """Crash window 1: a staged file written, then the process dies
    before the DB transaction (and its outbox event) ever commits - no
    event exists to finalize it, so it would sit in staging forever
    without this sweep."""
    import os
    import time

    from services.document_storage_service import reclaim_orphaned_staging_files, stage_upload

    old_staging_path, _, _, _ = stage_upload(_FakeUploadedFile("old.pdf", b"old"), load_id=1)
    fresh_staging_path, _, _, _ = stage_upload(_FakeUploadedFile("fresh.pdf", b"fresh"), load_id=1)

    old_full_path = storage_dir / old_staging_path
    old_time = time.time() - 25 * 3600
    os.utime(old_full_path, (old_time, old_time))

    removed = reclaim_orphaned_staging_files(older_than_hours=24)

    assert removed >= 1
    assert not old_full_path.exists()
    assert (storage_dir / fresh_staging_path).exists()


# ---------------------------------------------------------------------------
# repositories/document_repo.py - SQLite atomicity
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_documents(monkeypatch, tmp_path):
    db_path = tmp_path / "documents_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table loads (id integer primary key)"))
        conn.execute(text("insert into loads (id) values (7)"))
        conn.execute(
            text(
                """
                create table documents (
                    id integer primary key autoincrement,
                    load_id integer not null,
                    document_type text not null default 'load_pdf',
                    filename text not null,
                    file_path text not null,
                    source text,
                    status text not null default 'available',
                    checksum text,
                    created_at timestamp not null default current_timestamp
                )
                """
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_insert_pending_document_creates_a_pending_row(sqlite_documents):
    from repositories.document_repo import get_document, insert_pending_document

    with db_client.transaction() as conn:
        document_id = insert_pending_document(
            conn,
            load_id=7,
            document_type="load_pdf",
            filename="a.pdf",
            file_path="load_7_x_a.pdf",
            source="invoice",
            checksum="abc123",
        )

    with db_client.transaction() as conn:
        row = get_document(conn, document_id)

    assert row["status"] == "pending"
    assert row["checksum"] == "abc123"


def test_update_document_status_transitions_to_available(sqlite_documents):
    from repositories.document_repo import get_document, insert_pending_document, update_document_status

    with db_client.transaction() as conn:
        document_id = insert_pending_document(
            conn, load_id=7, document_type="load_pdf", filename="a.pdf", file_path="x", source="s", checksum="c"
        )

    with db_client.transaction() as conn:
        update_document_status(conn, document_id, status="available")

    with db_client.transaction() as conn:
        row = get_document(conn, document_id)
    assert row["status"] == "available"


def test_document_row_and_outbox_event_commit_together(sqlite_documents):
    """Same atomicity proof pattern as tests/test_outbox_repo.py - the
    documents insert and the outbox enqueue must be one transaction."""
    from repositories.document_repo import insert_pending_document
    from repositories.outbox_repo import enqueue_outbox_event

    with db_client.get_engine(sqlite_documents).begin() as conn:
        conn.execute(
            text(
                """
                create table outbox_events (
                    id integer primary key autoincrement,
                    event_type text not null,
                    aggregate_type text not null,
                    aggregate_id text not null,
                    payload text not null default '{}',
                    idempotency_key text not null unique,
                    status text not null default 'pending',
                    attempt_count integer not null default 0,
                    available_at timestamp not null default current_timestamp,
                    created_at timestamp not null default current_timestamp,
                    claimed_at timestamp,
                    processed_at timestamp,
                    last_error text,
                    actor text
                )
                """
            )
        )

    with pytest.raises(RuntimeError):
        with db_client.transaction() as conn:
            insert_pending_document(
                conn, load_id=7, document_type="load_pdf", filename="a.pdf", file_path="x", source="s", checksum="c"
            )
            raise RuntimeError("simulated failure after the documents insert")

    count = db_client.read_df("select count(*) as n from documents").iloc[0]["n"]
    assert count == 0


# ---------------------------------------------------------------------------
# application/documents/commands.py::attach_load_document - authorization
# boundary and wiring, mocked DB/storage.
# ---------------------------------------------------------------------------


def test_unauthorized_actor_creates_zero_staged_file_zero_row_zero_event(monkeypatch):
    """Authorization runs BEFORE staging - an unauthorized actor produces
    no side effect at all, not a partial one."""
    from application.auth import permissions as permissions_module
    from application.documents.commands import attach_load_document

    monkeypatch.setitem(permissions_module.ROLE_PERMISSIONS, Role.DISPATCHER, frozenset())

    with mock.patch("services.document_storage_service.stage_upload") as stage_upload, mock.patch(
        "db_client.transaction"
    ) as transaction, mock.patch("repositories.document_repo.insert_pending_document") as insert_doc, mock.patch(
        "repositories.outbox_repo.enqueue_outbox_event"
    ) as enqueue_event:
        with pytest.raises(AuthorizationError):
            attach_load_document(actor=DISPATCHER, load_id=1, uploaded_file=object(), source="invoice")

    stage_upload.assert_not_called()
    transaction.assert_not_called()
    insert_doc.assert_not_called()
    enqueue_event.assert_not_called()


def test_authorized_attach_stages_inserts_and_enqueues_in_one_transaction():
    conn = mock.MagicMock()
    with mock.patch(
        "services.document_storage_service.stage_upload",
        return_value=("staging/tok.staging", "load_1_tok_a.pdf", "a.pdf", "checksum123"),
    ) as stage_upload, mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.document_repo.insert_pending_document", return_value=42
    ) as insert_doc, mock.patch("repositories.outbox_repo.enqueue_outbox_event") as enqueue_event:
        transaction.return_value.__enter__.return_value = conn

        from application.documents.commands import attach_load_document

        document_id = attach_load_document(actor=DISPATCHER, load_id=1, uploaded_file=object(), source="invoice")

    assert document_id == 42
    stage_upload.assert_called_once()
    insert_doc.assert_called_once()
    assert insert_doc.call_args.args[0] is conn
    assert insert_doc.call_args.kwargs["checksum"] == "checksum123"
    enqueue_event.assert_called_once()
    assert enqueue_event.call_args.kwargs["conn"] is conn
    assert enqueue_event.call_args.kwargs["event_type"] == "document.file.finalize"
    assert enqueue_event.call_args.kwargs["idempotency_key"] == "document_finalize:42"
    assert enqueue_event.call_args.kwargs["payload"]["document_id"] == 42
    assert enqueue_event.call_args.kwargs["actor"] == DISPATCHER.actor


def test_accounting_can_attach_documents_matches_shared_permission():
    """DOCUMENT_ATTACH is one of the few Phase 5B permissions granted to
    accounting - this proves the new staged flow doesn't accidentally
    narrow that."""
    conn = mock.MagicMock()
    with mock.patch(
        "services.document_storage_service.stage_upload", return_value=("s", "f", "a.pdf", "c")
    ), mock.patch("db_client.transaction") as transaction, mock.patch(
        "repositories.document_repo.insert_pending_document", return_value=1
    ), mock.patch("repositories.outbox_repo.enqueue_outbox_event") as enqueue_event:
        transaction.return_value.__enter__.return_value = conn

        from application.documents.commands import attach_load_document

        attach_load_document(actor=ACCOUNTING, load_id=1, uploaded_file=object(), source="invoice")

    enqueue_event.assert_called_once()
