from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.exceptions import NotFoundError, ValidationError
from repositories import work_item_repo


@dataclass(frozen=True)
class UpdateDraftResult:
    ok: bool
    conversation_key: str
    updated_fields: list[str]


def update_order_draft(conversation_key: str, fields: dict[str, Any]) -> UpdateDraftResult:
    """Apply a dispatcher-submitted draft edit. Single transaction, one
    whitelisted UPDATE - see repositories.work_item_repo.
    DRAFT_EDITABLE_COLUMNS for why this doesn't need the automated-parser
    merge/precedence logic the email-ingestion path uses."""
    if not str(conversation_key or "").strip():
        raise ValidationError("conversation_key is required to update a draft.")

    updated_fields = [k for k in fields if k in work_item_repo.DRAFT_EDITABLE_COLUMNS]
    if not updated_fields:
        raise ValidationError(
            f"No editable draft fields supplied. Editable fields: {sorted(work_item_repo.DRAFT_EDITABLE_COLUMNS)}"
        )

    found = work_item_repo.update_order_draft_fields(conversation_key, fields)
    if not found:
        raise NotFoundError(f"No order draft found for conversation_key={conversation_key!r}.")

    return UpdateDraftResult(ok=True, conversation_key=conversation_key, updated_fields=updated_fields)
