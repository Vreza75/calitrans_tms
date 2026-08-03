from __future__ import annotations

from application.work_items.models import ConversationPage
from application.work_items.queries import get_conversation_page


def get_conversation_history(
    conversation_key: str,
    *,
    page: int = 1,
    page_size: int = 25,
) -> ConversationPage:
    """Full message history for one business conversation - only called
    when a dispatcher (or API client) explicitly requests it. Never part
    of the queue page or the work-item detail read."""
    return get_conversation_page(conversation_key, page=page, page_size=page_size)
