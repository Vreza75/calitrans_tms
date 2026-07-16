from __future__ import annotations

import pandas as pd

from db_client import read_df
from services.communications import email_provider, motive_provider, twilio_provider
from services.communications.base import SendResult
from services.dispatch_data_service import ensure_communications_schema

_TIMELINE_COLUMNS = ["created_at", "direction", "channel", "party", "message_body"]

_PROVIDERS = {
    "email": email_provider,
    "twilio": twilio_provider,
    "motive": motive_provider,
}


def send_message(channel: str, recipient: str, body: str, **kwargs) -> SendResult:
    """Routes to the provider module matching `channel`. Adding a new
    provider (WhatsApp, Slack, ...) means adding one entry to _PROVIDERS —
    nothing else in this function, or any caller, changes."""
    provider = _PROVIDERS.get(channel)
    if provider is None:
        return {
            "success": False,
            "provider_message_id": None,
            "error": f"Unknown communications channel: {channel!r}",
        }
    return provider.send_message(recipient, body, **kwargs)


def get_load_timeline(load_id: int) -> pd.DataFrame:
    """Read-only combined view of dispatch_messages (driver/internal) and
    load_communications (Gmail Operations Inbox customer email) for one
    load, normalized to a common shape and sorted newest first. Purely
    additive — no writes, no changes to load_communications or the
    Operations Inbox."""
    ensure_communications_schema()

    try:
        dispatch_df = read_df(
            """
            select created_at, direction, coalesce(provider, 'internal') as channel,
                   recipient as party, message_body
            from dispatch_messages
            where load_id = :load_id
            """,
            {"load_id": load_id},
        )
    except Exception:
        dispatch_df = pd.DataFrame(columns=_TIMELINE_COLUMNS)

    try:
        customer_df = read_df(
            """
            select created_at, direction, 'email' as channel, sender as party, message_body
            from load_communications
            where load_id = :load_id
            """,
            {"load_id": load_id},
        )
    except Exception:
        customer_df = pd.DataFrame(columns=_TIMELINE_COLUMNS)

    combined = pd.concat([dispatch_df, customer_df], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=_TIMELINE_COLUMNS)
    return combined.sort_values("created_at", ascending=False).reset_index(drop=True)
