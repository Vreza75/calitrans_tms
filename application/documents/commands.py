# application/documents/commands.py

from __future__ import annotations

"""Document-attachment mutation command - reused by both
pages_app/documents.py and pages_app/dispatch_board.py, which previously
each called DispatchDatabaseClient().attach_file_to_row() directly with
no authorization check at all.

Side-effect ordering note (see docs/AUTHENTICATION.md's Phase 5B
side-effect inventory): DispatchDatabaseClient.attach_file_to_row()
writes the uploaded file to local disk storage BEFORE inserting the
`documents` row - a crash between those two steps leaves an orphaned
file on disk with no DB record pointing to it (recoverable manually, not
silently lost data, but not atomic). This command does not change that
existing behavior - only adds the permission check in front of it."""

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission


def attach_load_document(*, actor: AuthenticatedActor, load_id: int, uploaded_file: Any, source: str = "streamlit") -> None:
    require_permission(actor, Permission.DOCUMENT_ATTACH)

    from db_client import DispatchDatabaseClient

    DispatchDatabaseClient().attach_file_to_row(load_id, uploaded_file, source=source)
