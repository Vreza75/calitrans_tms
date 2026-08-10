from __future__ import annotations

from services.dispatch_stages import SHARED_STAGES, get_operational_stages
from services.workflow_constants import normalize_service_flow

_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "Import": {
        "En Route to Pickup": "En Route to Port",
        "At Pickup": "At Port",
        "En Route to Delivery": "En Route to Delivery Warehouse",
        "At Delivery": "At Delivery Warehouse",
    },
    "Export": {
        "En Route to Pickup": "En Route to Pickup Warehouse",
        "At Pickup": "At Pickup Warehouse",
        "En Route to Delivery": "En Route to Port",
        "At Delivery": "At Port",
        "Completed": "In-Gated",
    },
    "Local Import": {
        "En Route to Pickup": "En Route to Origin Warehouse",
        "At Pickup": "At Origin Warehouse",
        "En Route to Delivery": "En Route to Destination Warehouse",
        "At Delivery": "At Destination Warehouse",
    },
}
_DISPLAY_LABELS["Local Export"] = _DISPLAY_LABELS["Local Import"]


def get_display_label(move_type: str, canonical_status: str, *, via_empty_return: bool = False) -> str:
    """Contextual, move-type-specific label for a canonical status. Purely
    cosmetic — never stored. Falls back to the canonical status itself if
    this move type has no override for it."""
    normalized = normalize_service_flow(move_type, default="Local Import")
    if normalized == "Import" and canonical_status == "Completed" and via_empty_return:
        return "Empty Returned"
    return _DISPLAY_LABELS.get(normalized, {}).get(canonical_status, canonical_status)


def get_board_columns() -> list[str]:
    """Canonical stages are shared across move types now, so there's one
    status-filter option list. A move type that doesn't use a given stage
    (e.g. Export + Returning Empty) simply never has rows in it."""
    return list(SHARED_STAGES)


def is_active_dispatch_status(move_type: str, status: str) -> bool:
    stages = get_operational_stages(move_type)
    return status in stages and status != "Completed"


def get_next_action(
    move_type: str,
    status: str,
    *,
    has_driver: bool = False,
    empty_return_required: bool = False,
) -> tuple[str, str] | None:
    """Return (button_label, target_canonical_status) for the next valid
    operational action from this status, or None if there isn't one
    (Completed / Cancelled — no forward action)."""
    normalized = normalize_service_flow(move_type, default="Local Import")

    if status == "Ready to Dispatch":
        label = "Start En Route" if has_driver else "Assign & Start"
        return label, "En Route to Pickup"
    if status == "En Route to Pickup":
        return "Mark Arrived", "At Pickup"
    if status == "At Pickup":
        label = "Mark Container Picked Up" if normalized == "Import" else "Mark Loaded / Picked Up"
        return label, "En Route to Delivery"
    if status == "En Route to Delivery":
        return "Mark Arrived", "At Delivery"
    if status == "At Delivery":
        if normalized == "Import" and empty_return_required:
            return "Start Empty Return", "Returning Empty"
        if normalized == "Import":
            return "Complete Dispatch", "Completed"
        if normalized == "Export":
            return "Mark In-Gated", "Completed"
        return "Mark Delivered", "Completed"
    if status == "Returning Empty":
        return "Mark Empty Returned", "Completed"
    return None
