from __future__ import annotations

import pandas as pd

from db_client import DispatchDatabaseClient, execute, read_df, require_schema_ready

def _read_status_timeline(load_id: int) -> pd.DataFrame:
    try:
        return read_df(
            """
            select old_status, new_status, notes, created_by, created_at
            from status_events
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()

def _read_dispatch_messages(load_id: int) -> pd.DataFrame:
    try:
        return read_df(
            """
            select message_type, direction, recipient, message_body, sent_by, created_at
            from dispatch_messages
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()

def _read_documents_for_load(load_id: int) -> pd.DataFrame:
    """`status` (Phase 6B - 'pending'/'available'/'failed') is included so
    callers can distinguish a document whose file is actually stored from
    one still staging or that failed to finalize -
    services.dispatch_workflow_service::_load_document_count filters on
    it; the raw Documents tab shows every status so the state is visible,
    not hidden."""
    try:
        return read_df(
            """
            select document_type, filename, source, status, created_at
            from documents
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()

def _update_load_extra_fields(load_id: int, current_location: str, eta_value, live_load_status: str, live_unload_status: str) -> None:
    execute(
        """
        update loads
        set current_location = :current_location,
            eta = :eta,
            live_load_status = :live_load_status,
            live_unload_status = :live_unload_status,
            last_driver_update = now()
        where id = :load_id
        """,
        {
            "load_id": load_id,
            "current_location": current_location or None,
            "eta": eta_value or None,
            "live_load_status": live_load_status or None,
            "live_unload_status": live_unload_status or None,
        },
    )

def ensure_communications_schema() -> None:
    """Verify dispatch_messages has the columns the Communications
    Engine's provider-agnostic layer needs (provider, delivery/read
    status, attachments, metadata, provider message id) - see
    database/communications_foundation_migration.sql, which is the sole
    owner of this schema. Raises SchemaNotReadyError if that migration
    has not been applied; never runs DDL itself.

    Safe to call on every insert/read: require_schema_ready() is a single
    cheap round trip and this app's traffic (~10-20 drivers, one
    dispatcher) never makes that a bottleneck. No st.session_state
    caching here — services/ modules must not import streamlit
    (CLAUDE.md)."""
    require_schema_ready(
        "dispatch_messages", "provider_message_id", migration_hint="database/communications_foundation_migration.sql"
    )

def _insert_dispatch_message(
    load_id: int,
    message_type: str,
    direction: str,
    recipient: str,
    message_body: str,
    provider: str = "internal",
    sent_by: str = "dispatcher",
    *,
    conn=None,
    delivery_status: str | None = None,
) -> int | None:
    """`sent_by` defaults to the pre-existing literal "dispatcher" for
    every caller that doesn't pass it (zero behavior change) -
    application-command callers pass the real AuthenticatedActor.actor so
    dispatch_messages reflects who actually sent it.

    `conn`/`delivery_status` are Phase 6 additions, both optional and
    both None by default (zero behavior change for every pre-Phase-6
    caller, which keeps using the module-level execute() and gets back
    None as before). Pass `conn` to run this insert as part of a larger
    db_client.transaction() - see application/loads/commands.py::
    mark_load_ready_to_dispatch, which inserts this row and its outbox
    event in the same transaction. Only when `conn` is supplied does this
    function return the inserted row's id (via RETURNING), so the caller
    can reference it from an outbox event payload."""
    ensure_communications_schema()
    sql = """
        insert into dispatch_messages
            (load_id, message_type, direction, recipient, message_body, sent_by, provider, delivery_status)
        values
            (:load_id, :message_type, :direction, :recipient, :message_body, :sent_by, :provider, :delivery_status)
        """
    params = {
        "load_id": load_id,
        "message_type": message_type,
        "direction": direction,
        "recipient": recipient or None,
        "message_body": message_body,
        "sent_by": sent_by,
        "provider": provider,
        "delivery_status": delivery_status,
    }
    if conn is not None:
        from sqlalchemy import text

        result = conn.execute(text(sql + " returning id"), params)
        return int(result.scalar_one())

    execute(sql, params)
    return None


def update_dispatch_message_delivery(
    message_id: int, *, conn, delivery_status: str, provider_message_id: str | None = None
) -> None:
    """Project an outbox delivery outcome onto the dispatch_messages row
    it accompanies. Called only by services.outbox_processor - must run
    in the same short transaction as the corresponding outbox_events
    status write (see repositories/outbox_repo.py's mark_delivered/
    mark_retry/mark_failed), so the audit row and the queue state never
    disagree about whether delivery happened."""
    from sqlalchemy import text

    conn.execute(
        text(
            """
            update dispatch_messages
            set delivery_status = :delivery_status,
                provider_message_id = coalesce(:provider_message_id, provider_message_id)
            where id = :id
            """
        ),
        {"id": message_id, "delivery_status": delivery_status, "provider_message_id": provider_message_id},
    )

def _save_status_quick_update(load_id: int, selected_load, new_status: str, note: str) -> tuple[bool, str]:
    old_status = str(selected_load.get("Status", "") or "")

    DispatchDatabaseClient().update_row_fields(
        load_id,
        {
            "Status": new_status,
            "Dispatcher Notes": note,
        },
    )
    _insert_dispatch_message(
        load_id,
        "driver_status_quick_update",
        "internal",
        "dispatcher",
        f"Quick status update: {new_status}. {note}",
    )

    from services.customer_status_email_service import _send_customer_status_update_email

    return _send_customer_status_update_email(load_id, selected_load, old_status, new_status, note)

