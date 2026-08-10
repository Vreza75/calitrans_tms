from __future__ import annotations

import requests

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


def send_sms(to_phone: str, message: str) -> tuple[bool, str]:
    """Send `message` to `to_phone` via Twilio. Returns (success, sid_or_error).

    Never raises — every failure (missing secrets, network error, non-2xx
    response) is reported as (False, reason) so the caller can show it to
    the dispatcher without a stack trace.
    """
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    from_number = get_secret("TWILIO_FROM_NUMBER")

    if not account_sid or not auth_token or not from_number:
        return False, "Twilio is not configured (missing TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)."

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"

    try:
        response = requests.post(
            url,
            auth=(account_sid, auth_token),
            data={"To": to_phone, "From": from_number, "Body": message},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach Twilio: {exc}"

    if response.status_code in (200, 201):
        try:
            sid = response.json().get("sid", "")
        except ValueError:
            sid = ""
        return True, sid

    try:
        error_detail = response.json().get("message", response.text)
    except ValueError:
        error_detail = response.text

    return False, f"Twilio error ({response.status_code}): {error_detail}"
