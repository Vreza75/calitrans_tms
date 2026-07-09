from __future__ import annotations

"""Thin compatibility wrapper for Operations Inbox email sync.

The active implementation lives in services.operations_inbox_service so the
Operations Inbox page and any older imports use the same sync/dedupe logic.
"""

import services.operations_inbox_service as ops


def sync_operations_email_engine(*args, **kwargs) -> dict:
    return ops.sync_operations_email_engine(*args, **kwargs)


def sync_conversation_status(conversation_key: str) -> None:
    return ops.sync_conversation_status(conversation_key)


def find_existing_operations_email_record(
    message_id: str,
    subject: str,
    sender: str,
    received_at: str | None = None,
) -> dict | None:
    return ops.find_existing_operations_email_record(message_id, subject, sender, received_at)


def load_existing_operations_email_lookup(limit: int = 5000) -> dict:
    return ops.load_existing_operations_email_lookup(limit=limit)


def operations_email_already_imported(
    message_id: str,
    subject: str,
    sender: str,
    received_at: str | None = None,
) -> bool:
    return ops.operations_email_already_imported(
        message_id,
        subject=subject,
        sender=sender,
        received_at=received_at,
    )


_sync_operations_email_engine = sync_operations_email_engine
_sync_conversation_status = sync_conversation_status
_find_existing_operations_email_record = find_existing_operations_email_record
_load_existing_operations_email_lookup = load_existing_operations_email_lookup
_operations_email_already_imported = operations_email_already_imported
