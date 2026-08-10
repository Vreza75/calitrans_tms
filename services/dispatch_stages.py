from __future__ import annotations

from services.workflow_constants import normalize_service_flow

COMPLETION_STATUS = "Completed"
CANCELLED_STATUS = "Cancelled"

SHARED_STAGES = [
    "Ready to Dispatch",
    "En Route to Pickup",
    "At Pickup",
    "En Route to Delivery",
    "At Delivery",
    "Returning Empty",
    "Completed",
]

_MOVE_TYPES_WITH_EMPTY_RETURN = {"Import"}


def get_operational_stages(move_type: str) -> list[str]:
    normalized = normalize_service_flow(move_type, default="Local Import")
    if normalized in _MOVE_TYPES_WITH_EMPTY_RETURN:
        return list(SHARED_STAGES)
    return [stage for stage in SHARED_STAGES if stage != "Returning Empty"]


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

    Hard business rules (driver+truck required to start moving, an origin
    required to move, "At X" requires having passed through "En Route to
    X" first, Returning Empty requires At Delivery, Completed requires the
    move type's milestone) always apply, override or not. override only
    bypasses the completed-load lock and the generic forward-skip /
    backward-move sequencing guard.
    """
    stages = get_operational_stages(move_type)

    if new_status == CANCELLED_STATUS:
        if current_status == COMPLETION_STATUS:
            return False, "Cannot cancel a load that is already Completed."
        return True, ""

    if new_status not in stages:
        return False, f"'{new_status}' is not a valid operational status for {move_type}."

    if current_status in (COMPLETION_STATUS, CANCELLED_STATUS) and not override:
        return False, f"Load is {current_status}; further operational status changes require an override."

    current_index = _stage_index(stages, current_status)
    new_index = stages.index(new_status)

    if new_status == "En Route to Pickup" and not (has_driver and has_truck):
        return False, "Driver and truck must be assigned before starting En Route to Pickup."

    if new_status.startswith("En Route") and not has_origin:
        return False, f"Cannot move to '{new_status}' without a valid origin."

    _preceding_enroute = {"At Pickup": "En Route to Pickup", "At Delivery": "En Route to Delivery"}
    if new_status in _preceding_enroute:
        required = _preceding_enroute[new_status]
        required_index = stages.index(required)
        if current_index is None or current_index < required_index:
            return False, f"Cannot move to '{new_status}' before '{required}'."

    if new_status == "Returning Empty":
        if "Returning Empty" not in stages:
            return False, "Returning Empty does not apply to this move type."
        delivery_index = stages.index("At Delivery")
        if current_index is None or current_index < delivery_index:
            return False, "Cannot start empty return before the load has reached At Delivery."

    if new_status == COMPLETION_STATUS:
        if empty_return_required and "Returning Empty" in stages:
            milestone = "Returning Empty"
        else:
            milestone = "At Delivery"
        milestone_index = stages.index(milestone)
        if current_index is None or current_index < milestone_index:
            return False, f"Cannot mark Completed before reaching '{milestone}'."

    if not override and current_index is not None and new_status != COMPLETION_STATUS:
        if new_index > current_index + 1:
            return False, f"Cannot skip from '{current_status}' directly to '{new_status}' without an override."
        if new_index < current_index:
            return False, f"Cannot move backward from '{current_status}' to '{new_status}' without an override."

    return True, ""
