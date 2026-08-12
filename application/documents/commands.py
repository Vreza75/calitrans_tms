# application/documents/commands.py

from __future__ import annotations

"""Document-attachment mutation command - reused by both
pages_app/documents.py and pages_app/dispatch_board.py, which previously
each called DispatchDatabaseClient().attach_file_to_row() directly with
no authorization check at all.

Phase 6B (see docs/architecture/DOCUMENT_LIFECYCLE.md): the pre-existing
flow wrote the uploaded file to local disk storage BEFORE inserting the
`documents` row - a crash between those two steps left an orphaned file
on disk with no DB record pointing to it. Fixed here with the same
staged-write + atomic-rename + outbox pattern Phase 6 built for SMS:
require_permission runs first (before any staging/write - an
unauthorized actor produces zero staged file, zero DB row, zero outbox
event); the upload is staged to a temp path and checksummed
(services/document_storage_service.py::stage_upload); a `documents` row
is inserted with status='pending' and a 'document.file.finalize' outbox
event enqueued, both in one transaction; services/outbox_processor.py
finalizes the file (atomic rename) and flips status to 'available' only
once that succeeds - the DB never claims a document is available before
its file is actually there.

DispatchDatabaseClient.attach_file_to_row() (db_client.py) is
deliberately NOT touched or reused here - it remains exactly as it was,
still used only by the confirmed-dead pages_app/documents.py::
render_pdf_intake path (see tests/test_direct_mutation_scanner.py's
allowlist) - per this repo's dead-code-caution rule, that path is not
migrated because it has no live caller to protect."""

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission


def attach_load_document(*, actor: AuthenticatedActor, load_id: int, uploaded_file: Any, source: str = "streamlit") -> int:
    """Returns the new document's id (status='pending' until the outbox
    processor finalizes it - see this module's docstring)."""
    require_permission(actor, Permission.DOCUMENT_ATTACH)

    from db_client import transaction
    from repositories.document_repo import insert_pending_document
    from repositories.outbox_repo import enqueue_outbox_event
    from services.document_storage_service import stage_upload

    staging_path, final_storage_key, original_filename, checksum = stage_upload(uploaded_file, load_id=load_id)

    with transaction() as conn:
        document_id = insert_pending_document(
            conn,
            load_id=load_id,
            document_type="load_pdf",
            filename=original_filename,
            file_path=final_storage_key,
            source=source,
            checksum=checksum,
        )

        # One finalize attempt per document row, keyed on the row's own
        # id - unlike the SMS event's content+time-window key, a document
        # row is inherently a single, unique logical event (a fresh
        # attach always gets a fresh row via a fresh command call), so
        # there's no equivalent "legitimate resend of the same content"
        # ambiguity to guard against.
        enqueue_outbox_event(
            conn=conn,
            event_type="document.file.finalize",
            aggregate_type="document",
            aggregate_id=str(document_id),
            payload={
                "document_id": document_id,
                "staging_path": staging_path,
                "final_storage_key": final_storage_key,
                "checksum": checksum,
            },
            idempotency_key=f"document_finalize:{document_id}",
            actor=actor.actor,
        )

    return document_id
