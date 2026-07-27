from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


SELECTED_WORK_ITEM_ID = "selected_work_item_id"
SELECTED_WORK_ITEM_QUEUE = "selected_work_item_queue"
WORK_ITEM_DIALOG_OPEN = "work_item_dialog_open"


def initialize_work_item_state(state: MutableMapping[str, Any]) -> None:
    """Initialize the dispatcher dialog state without reopening a closed item."""
    state.setdefault(SELECTED_WORK_ITEM_ID, None)
    state.setdefault(SELECTED_WORK_ITEM_QUEUE, None)
    state.setdefault(WORK_ITEM_DIALOG_OPEN, False)


def open_work_item(
    state: MutableMapping[str, Any],
    *,
    work_item_id: int,
    queue_name: str,
) -> None:
    """Open a work item only in response to its explicit queue action."""
    state[SELECTED_WORK_ITEM_ID] = int(work_item_id)
    state[SELECTED_WORK_ITEM_QUEUE] = str(queue_name)
    state[WORK_ITEM_DIALOG_OPEN] = True


def close_work_item(state: MutableMapping[str, Any]) -> None:
    """Close the dialog and clear temporary state tied to the selected item."""
    selected_id = state.get(SELECTED_WORK_ITEM_ID)
    if selected_id is not None:
        attachment_prefixes = (
            f"operations_attachment_selection_{selected_id}",
            f"ops_attachment_selector_{selected_id}",
        )
        for key in list(state.keys()):
            if any(str(key).startswith(prefix) for prefix in attachment_prefixes):
                state.pop(key, None)

    state[SELECTED_WORK_ITEM_ID] = None
    state[SELECTED_WORK_ITEM_QUEUE] = None
    state[WORK_ITEM_DIALOG_OPEN] = False


def should_show_contextual_load_match(
    *,
    final_decision: dict[str, Any],
    matched_load_id: Any,
    load_match_candidates: list[dict[str, Any]] | None,
) -> bool:
    """Show matching only for unresolved existing-load update work."""
    return bool(
        final_decision.get("queue") == "Existing Load Updates"
        and matched_load_id is None
        and load_match_candidates
    )
