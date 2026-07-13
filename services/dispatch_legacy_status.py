from __future__ import annotations

from services.workflow_constants import normalize_service_flow

_MOVE_TYPE_ENROUTE_PICKUP_LEG = {
    "Import": "En Route to Port",
    "Export": "En Route to Pickup Warehouse",
    "Local Import": "En Route to Origin Warehouse",
    "Local Export": "En Route to Origin Warehouse",
}

_MOVE_TYPE_AT_PICKUP_LEG = {
    "Import": "At Port",
    "Export": "At Pickup Warehouse",
    "Local Import": "At Origin Warehouse",
    "Local Export": "At Origin Warehouse",
}

_MOVE_TYPE_LOADED_LEG = {
    "Import": "Container Picked Up",
    "Export": "Container Loaded",
    "Local Import": "Loaded / Picked Up",
    "Local Export": "Loaded / Picked Up",
}

_MOVE_TYPE_ENROUTE_DELIVERY_LEG = {
    "Import": "En Route to Delivery Warehouse",
    "Export": "En Route to Port",
    "Local Import": "En Route to Destination Warehouse",
    "Local Export": "En Route to Destination Warehouse",
}

_PRE_DISPATCH_STATUSES = {
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Awaiting Appointment",
    "New Email",
    "Needs Review",
    "Order Created",
}

_DIRECT_MAP = {
    "Ready to Dispatch": "Ready to Dispatch",
    "Driver Assigned": "Driver Assigned",
    "Assigned": "Driver Assigned",
}


def map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]:
    """Map a legacy loads.status value to (new operational status, closeout_stage).

    Returns ("", "Not Started") for statuses that predate operational
    dispatch (order intake/verification) — those don't belong on
    loads.status in the new model; callers decide what to do with them
    (this function only defines the mapping, it doesn't migrate rows).
    """
    move_type = normalize_service_flow(move_type, default="Local Import")
    status = (old_status or "").strip()

    if status == "Cancelled":
        return "Cancelled", "Not Started"

    if status in _PRE_DISPATCH_STATUSES:
        return "", "Not Started"

    if status in _DIRECT_MAP:
        return _DIRECT_MAP[status], "Not Started"

    if status in ("Dispatched", "En Route to Pickup"):
        return _MOVE_TYPE_ENROUTE_PICKUP_LEG[move_type], "Not Started"

    if status == "At Port":
        return "At Port", "Not Started"

    if status == "At Pickup":
        return _MOVE_TYPE_AT_PICKUP_LEG[move_type], "Not Started"

    if status in ("Loaded / Picked Up", "Loaded"):
        return _MOVE_TYPE_LOADED_LEG[move_type], "Not Started"

    if status == "En Route To Delivery":
        return _MOVE_TYPE_ENROUTE_DELIVERY_LEG[move_type], "Not Started"

    if status == "Delivered":
        return "Delivered", "POD Needed"

    if status == "Returning Empty":
        return "Returning Empty", "POD Needed"

    if status == "POD Received":
        return "Dispatch Complete", "POD Received"

    if status in ("Ready for ProfitTools", "Exported to ProfitTools"):
        return "Dispatch Complete", "Ready for ProfitTools"

    if status in ("Invoiced", "Closed"):
        return "Dispatch Complete", "Closed"

    return "", "Not Started"
