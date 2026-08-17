# repositories/document_repo.py

from __future__ import annotations

"""Phase 6B: documents table persistence for the staged/atomic
attach-document flow (see services/document_storage_service.py and
application/documents/commands.py::attach_load_document). Framework-
neutral - no Streamlit import.

Distinct from db_client.py::DispatchDatabaseClient.attach_file_to_row,
which remains unchanged and is still used by the confirmed-dead
pages_app/documents.py::render_pdf_intake path only (see
tests/test_direct_mutation_scanner.py's allowlist) - not touched here,
per this repo's dead-code-caution rule (CLAUDE.md)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def insert_pending_document(
    conn: Connection,
    *,
    load_id: int,
    document_type: str,
    filename: str,
    file_path: str,
    source: str,
    checksum: str,
) -> int:
    """Insert a documents row with status='pending' - the file at
    file_path does not exist there yet (it's still staged elsewhere);
    the outbox finalize event, enqueued by the caller in the same
    transaction, is what actually puts a file at that path. Returns the
    new row's id, for the caller to reference in that outbox event's
    payload/idempotency_key."""
    result = conn.execute(
        text(
            """
            insert into documents (load_id, document_type, filename, file_path, source, status, checksum)
            values (:load_id, :document_type, :filename, :file_path, :source, 'pending', :checksum)
            returning id
            """
        ),
        {
            "load_id": load_id,
            "document_type": document_type,
            "filename": filename,
            "file_path": file_path,
            "source": source,
            "checksum": checksum,
        },
    )
    return int(result.scalar_one())


def update_document_status(conn: Connection, document_id: int, *, status: str) -> None:
    conn.execute(
        text("update documents set status = :status where id = :id"),
        {"id": document_id, "status": status},
    )


def get_document(conn: Connection, document_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            select id, load_id, document_type, filename, file_path, source, status, checksum, created_at
            from documents where id = :id
            """
        ),
        {"id": document_id},
    ).mappings().first()
    return dict(row) if row else None


def list_documents_for_load(load_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """Phase 8: metadata-only read for a load's document list API
    (GET /api/v1/loads/{load_id}/documents) - deliberately excludes
    `file_path` (a storage key, not a field the API should ever return;
    STEP 12: "Do not expose unrestricted local storage paths" - same
    rule application/work_items/models.py::AttachmentMeta.attachment_ref
    already follows for Operations Inbox attachments). No `size`/
    `available_at` fields - the `documents` table has no such columns
    (database/schema.sql + database/document_lifecycle_migration.sql);
    not fabricated (STEP 5's "only implement fields actually present"
    rule, applied here too). Framework-neutral: uses db_client.read_df,
    not a caller-supplied conn - this is a standalone read, not part of
    a larger transaction."""
    from db_client import read_df

    df = read_df(
        """
        select id, document_type, filename, source, status, created_at
        from documents
        where load_id = :load_id
        order by created_at desc
        limit :limit
        """,
        {"load_id": int(load_id), "limit": max(min(int(limit or 50), 200), 1)},
    )
    return df.to_dict(orient="records")
