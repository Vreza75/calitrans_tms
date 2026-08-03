from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from application.work_items.models import ConversationPage


class ConversationMessageOut(BaseModel):
    id: int
    source_received_at: datetime | None
    email_direction: str
    source_sender: str
    source_subject: str
    message_preview: str
    conversation_status: str


class ConversationPageOut(BaseModel):
    conversation_key: str
    messages: list[ConversationMessageOut]
    total_messages: int

    @classmethod
    def from_domain(cls, page: ConversationPage) -> "ConversationPageOut":
        return cls(
            conversation_key=page.conversation_key,
            messages=[ConversationMessageOut(**m.__dict__) for m in page.messages],
            total_messages=page.total_messages,
        )
