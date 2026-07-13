from __future__ import annotations

from services.workflow_constants import normalize_service_flow

COMPLETION_STATUS = "Dispatch Complete"
CANCELLED_STATUS = "Cancelled"

OPERATIONAL_STAGES: dict[str, list[str]] = {
    "Import": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Port",
        "At Port",
        "Container Picked Up",
        "En Route to Delivery Warehouse",
        "At Delivery Warehouse",
        "Delivered",
        "Returning Empty",
        "Empty Returned",
        "Dispatch Complete",
    ],
    "Export": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Pickup Warehouse",
        "At Pickup Warehouse",
        "Container Loaded",
        "En Route to Port",
        "At Port",
        "In-Gated",
        "Dispatch Complete",
    ],
    "Local Import": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ],
    "Local Export": [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ],
}

CLOSEOUT_STAGES = [
    "Not Started",
    "POD Needed",
    "POD Received",
    "Documents Review",
    "Accessorial Review",
    "Rate Verification",
    "Ready to Invoice",
    "Invoice Sent",
    "Ready for ProfitTools",
    "Closed",
]

# Status a load must have reached (or passed) before it can become
# Dispatch Complete, per move type. Import overrides this to "Empty
# Returned" when empty_return_required=True (see validate_transition).
_COMPLETION_MILESTONE = {
    "Import": "Delivered",
    "Export": "In-Gated",
    "Local Import": "Delivered",
    "Local Export": "Delivered",
}

_ASSIGN_GATED_STATUSES = {"Driver Assigned"}
_AT_LOCATION_STATUSES = {"At Port", "At Pickup Warehouse", "At Origin Warehouse"}


def get_operational_stages(move_type: str) -> list[str]:
    normalized = normalize_service_flow(move_type, default="Local Import")
    return OPERATIONAL_STAGES.get(normalized, OPERATIONAL_STAGES["Local Import"])


def _stage_index(stages: list[str], status: str) -> int | None:
    try:
        return stages.index(status)
    except ValueError:
        return None


def validate_transition(
    move_type: str,
    current_status: str,
    new_status: str,
    *,
    has_driver: bool = False,
    has_truck: bool = False,
    has_origin: bool = False,
    empty_return_required: bool = False,
    override: bool = False,
) -> tuple[bool, str]:
    """Return (is_valid, reason). reason is "" when valid.

    override=True bypasses the completed-load lock and the forward-skip /
    backward-move guard, but never bypasses the hard business rules
    (assignment required, origin required, Returning Empty requires
    Delivered, In-Gated requires At Port, Dispatch Complete requires
    reaching the move type's completion milestone) — those always apply.
    """
    stages = get_operational_stages(move_type)

    if new_status == CANCELLED_STATUS:
        if current_status == COMPLETION_STATUS:
            return False, "Cannot cancel a load that is already Dispatch Complete."
        return True, ""

    if new_status not in stages:
        return False, f"'{new_status}' is not a valid operational status for {move_type}."

    if current_status in (COMPLETION_STATUS, CANCELLED_STATUS) and not override:
        return False, f"Load is {current_status}; further operational status changes require an override."

    current_index = _stage_index(stages, current_status)
    new_index = stages.index(new_status)
    assign_index = _stage_index(stages, "Driver Assigned")

    if new_status in _ASSIGN_GATED_STATUSES and not (has_driver and has_truck):
        return False, "Driver and truck must be assigned before this status."

    if new_status.startswith("En Route") and not has_origin:
        return False, f"Cannot move to '{new_status}' without a valid origin."

    if new_status in _AT_LOCATION_STATUSES and assign_index is not None:
        if current_index is None or current_index < assign_index:
            return False, f"Cannot move to '{new_status}' before the load has been assigned."

    if move_type == "Import" and new_status == "Returning Empty":
        delivered_index = stages.index("Delivered")
        if current_index is None or current_index < delivered_index:
            return False, "Cannot start empty return before the load has been Delivered."

    if move_type == "Export" and new_status == "In-Gated":
        at_port_index = stages.index("At Port")
        if current_index is None or current_index < at_port_index:
            return False, "Cannot mark In-Gated before the load has reached the port."

    if new_status == COMPLETION_STATUS:
        milestone = _COMPLETION_MILESTONE.get(move_type, "Delivered")
        if move_type == "Import" and empty_return_required:
            milestone = "Empty Returned"
        milestone_index = stages.index(milestone)
        if current_index is None or current_index < milestone_index:
            return False, f"Cannot mark Dispatch Complete before reaching '{milestone}'."

    if not override and current_index is not None and new_status != COMPLETION_STATUS:
        if new_index > current_index + 1:
            return False, f"Cannot skip from '{current_status}' directly to '{new_status}' without an override."
        if new_index < current_index:
            return False, f"Cannot move backward from '{current_status}' to '{new_status}' without an override."

    return True, ""
