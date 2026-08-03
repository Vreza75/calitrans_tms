from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from application.order_drafts.models import OrderDraftSummary


class OrderDraftOut(BaseModel):
    exists: bool
    draft_status: str | None = None
    booking_number: str | None = None
    container_number: str | None = None
    container_qty: int | None = None
    service_flow: str | None = None

    @classmethod
    def from_domain(cls, draft: OrderDraftSummary) -> "OrderDraftOut":
        return cls(**draft.__dict__)


class UpdateOrderDraftIn(BaseModel):
    fields: dict[str, Any]


class UpdateOrderDraftOut(BaseModel):
    ok: bool
    conversation_key: str
    updated_fields: list[str]
