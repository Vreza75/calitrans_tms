from __future__ import annotations

from services.communications.base import SendResult

_NOT_CONFIGURED_ERROR = (
    "Motive is not yet configured — no API credentials available yet. "
    "See Phase 2 of the Communications Engine plan "
    "(docs/superpowers/specs/2026-07-16-communications-engine-foundation-design.md)."
)


def send_message(recipient: str, body: str, **kwargs) -> SendResult:
    return {"success": False, "provider_message_id": None, "error": _NOT_CONFIGURED_ERROR}


def get_status(provider_message_id: str) -> str:
    return "unknown"


def get_delivery_receipts(provider_message_id: str) -> dict:
    return {}
