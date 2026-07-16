from __future__ import annotations

from services.communications.base import SendResult
from services.customer_status_email_service import _send_smtp_email


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    """Email provider adapter. Requires `subject` in kwargs — plain SMTP
    has no subject-less send path. `from_email`/`cc_email` are optional
    passthroughs to `_send_smtp_email`."""
    subject = kwargs.get("subject", "")
    from_email = kwargs.get("from_email", "")
    cc_email = kwargs.get("cc_email", "")
    try:
        _send_smtp_email(recipient, subject, body, from_email=from_email, cc_email=cc_email)
        return {"success": True, "provider_message_id": None, "error": None}
    except Exception as exc:
        return {"success": False, "provider_message_id": None, "error": str(exc)}


def get_status(provider_message_id: str) -> str:
    """Plain SMTP has no post-send delivery tracking in this codebase."""
    return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    """Plain SMTP has no delivery-receipt API in this codebase."""
    return {}
