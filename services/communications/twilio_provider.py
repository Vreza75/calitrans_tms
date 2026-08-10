from __future__ import annotations

import requests

from config import get_secret
from services.communications.base import SendResult
from services.driver_sms_service import TWILIO_API_BASE, format_phone_e164, send_sms


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    normalized = format_phone_e164(recipient)
    if not normalized:
        return {
            "success": False,
            "provider_message_id": None,
            "error": f"'{recipient}' is not a valid phone number.",
        }
    sent, sid_or_error = send_sms(normalized, body)
    if sent:
        return {"success": True, "provider_message_id": sid_or_error, "error": None}
    return {"success": False, "provider_message_id": None, "error": sid_or_error}


def get_status(provider_message_id: str) -> str:
    """Looks up a previously sent message's delivery status via Twilio's
    Messages API. Returns 'unknown' on any failure or missing config —
    never raises, mirroring send_sms's error-handling convention."""
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not provider_message_id:
        return "unknown"
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages/{provider_message_id}.json"
    try:
        response = requests.get(url, auth=(account_sid, auth_token), timeout=15)
        if response.status_code != 200:
            return "unknown"
        return response.json().get("status", "unknown")
    except requests.RequestException:
        return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    """Returns Twilio's status/error fields for a sent message, or {} on
    any failure or missing config — never raises."""
    account_sid = get_secret("TWILIO_ACCOUNT_SID")
    auth_token = get_secret("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not provider_message_id:
        return {}
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages/{provider_message_id}.json"
    try:
        response = requests.get(url, auth=(account_sid, auth_token), timeout=15)
        if response.status_code != 200:
            return {}
        data = response.json()
        return {
            "status": data.get("status"),
            "error_code": data.get("error_code"),
            "error_message": data.get("error_message"),
            "date_sent": data.get("date_sent"),
        }
    except requests.RequestException:
        return {}
