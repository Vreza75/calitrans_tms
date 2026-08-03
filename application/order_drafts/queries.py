from __future__ import annotations

from application.order_drafts.models import OrderDraftSummary
from repositories import work_item_repo


def get_order_draft(conversation_key: str) -> OrderDraftSummary:
    """Read-only pending-order-draft projection for one business
    conversation. Does not merge, validate, or write draft fields - that
    authority stays with the existing pending-draft workflow in
    pages_app/operations_inbox.py (one canonical pending-order-draft
    merge function, per project rules)."""
    row = work_item_repo.get_order_draft(conversation_key)
    if not row:
        return OrderDraftSummary(exists=False)

    return OrderDraftSummary(
        exists=True,
        draft_status=row.get("draft_status"),
        booking_number=row.get("booking_number"),
        container_number=row.get("container_number"),
        container_qty=row.get("container_qty"),
        service_flow=row.get("service_flow"),
    )
