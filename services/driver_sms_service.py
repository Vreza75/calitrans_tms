from __future__ import annotations

from config import get_secret

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def format_phone_e164(phone) -> str | None:
    """Normalize a free-text US/Canada phone number to +1XXXXXXXXXX.

    Returns None if the input can't produce a plausible 10-digit US number
    — callers must treat None as "cannot send," never guess or truncate.
    """
    digits = "".join(ch for ch in str(phone or "") if ch in "0123456789")

    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None
