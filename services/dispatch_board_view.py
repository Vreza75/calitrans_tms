from __future__ import annotations

from services.dispatch_stages import COMPLETION_STATUS, get_operational_stages
from services.workflow_constants import normalize_service_flow

SHARED_BOARD_STAGES = [
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "En Route to Delivery",
    "At Delivery",
    "Empty Return",
    "Completed",
]

_SHARED_STAGE_MAP: dict[str, dict[str, str]] = {
    "Import": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Port": "En Route to Pickup",
        "At Port": "At Pickup",
        "Container Picked Up": "At Pickup",
        "En Route to Delivery Warehouse": "En Route to Delivery",
        "At Delivery Warehouse": "At Delivery",
        "Delivered": "At Delivery",
        "Returning Empty": "Empty Return",
        "Empty Returned": "Empty Return",
        "Dispatch Complete": "Completed",
    },
    "Export": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Pickup Warehouse": "En Route to Pickup",
        "At Pickup Warehouse": "At Pickup",
        "Container Loaded": "At Pickup",
        "En Route to Port": "En Route to Delivery",
        "At Port": "At Delivery",
        "In-Gated": "Completed",
        "Dispatch Complete": "Completed",
    },
    "Local Import": {
        "Ready to Dispatch": "Ready to Dispatch",
        "Driver Assigned": "Assigned",
        "En Route to Origin Warehouse": "En Route to Pickup",
        "At Origin Warehouse": "At Pickup",
        "Loaded / Picked Up": "At Pickup",
        "En Route to Destination Warehouse": "En Route to Delivery",
        "At Destination Warehouse": "At Delivery",
        "Delivered": "At Delivery",
        "Dispatch Complete": "Completed",
    },
}
_SHARED_STAGE_MAP["Local Export"] = _SHARED_STAGE_MAP["Local Import"]


def to_shared_stage(move_type: str, status: str) -> str:
    """Map a move-type-specific operational status to one of the 8 shared
    board buckets, for the "All Service Flows" board view. Returns "" for
    a status this move type doesn't recognize."""
    normalized = normalize_service_flow(move_type, default="Local Import")
    mapping = _SHARED_STAGE_MAP.get(normalized, _SHARED_STAGE_MAP["Local Import"])
    return mapping.get(status, "")


def get_board_columns(service_flow_filter: str) -> list[str]:
    """Column set for the active board: the 8 shared buckets when viewing
    all service flows, or that move type's exact operational stage
    sequence when filtered to one specific flow."""
    if service_flow_filter == "All":
        return list(SHARED_BOARD_STAGES)
    return get_operational_stages(service_flow_filter)


def is_active_dispatch_status(move_type: str, status: str) -> bool:
    """True once a load has reached Ready to Dispatch and hasn't yet
    reached Dispatch Complete — i.e. belongs on the active Dispatch Board."""
    stages = get_operational_stages(move_type)
    return status in stages and status != COMPLETION_STATUS
