from __future__ import annotations

import pandas as pd

from db_client import DispatchDatabaseClient, column_exists, execute, read_df

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
    try:
        return read_df(
            """
            select document_type, filename, file_path, source, created_at
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
    """Idempotently extends dispatch_messages with the columns the
    Communications Engine's provider-agnostic layer needs (provider,
    delivery/read status, attachments, metadata, provider message id).
    Safe to call on every insert/read: column_exists() is a single cheap
    round trip and this app's traffic (~10-20 drivers, one dispatcher)
    never makes that a bottleneck. No st.session_state caching here —
    services/ modules must not import streamlit (CLAUDE.md)."""
    if column_exists("dispatch_messages", "provider_message_id"):
        return
    execute("alter table dispatch_messages add column if not exists provider text not null default 'internal'")
    execute("alter table dispatch_messages add column if not exists delivery_status text")
    execute("alter table dispatch_messages add column if not exists read_status text")
    execute("alter table dispatch_messages add column if not exists attachments jsonb")
    execute("alter table dispatch_messages add column if not exists metadata jsonb")
    execute("alter table dispatch_messages add column if not exists provider_message_id text")

def _insert_dispatch_message(load_id: int, message_type: str, direction: str, recipient: str, message_body: str) -> None:
    execute(
        """
        insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, sent_by)
        values (:load_id, :message_type, :direction, :recipient, :message_body, 'dispatcher')
        """,
        {
            "load_id": load_id,
            "message_type": message_type,
            "direction": direction,
            "recipient": recipient or None,
            "message_body": message_body,
        },
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

