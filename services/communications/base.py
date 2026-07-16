from __future__ import annotations

from typing import Protocol, TypedDict


class SendResult(TypedDict):
    success: bool
    provider_message_id: str | None
    error: str | None


class CommunicationProvider(Protocol):
    """Structural interface every communications provider module conforms
    to. No inheritance required — a provider module just needs functions
    matching these signatures (see services/communications/email_provider.py,
    twilio_provider.py, motive_provider.py). This lets a future provider
    (WhatsApp, Slack, etc.) be added without changing communications_service.py
    or anything that calls it."""

    def send_message(self, recipient: str, body: str, **kwargs) -> SendResult: ...

    def get_status(self, provider_message_id: str) -> str: ...

    def get_delivery_receipts(self, provider_message_id: str) -> dict: ...
