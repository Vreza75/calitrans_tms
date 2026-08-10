from __future__ import annotations

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

# Legacy status -> (new shared canonical status, closeout_stage). No longer
# branches on move_type for the target value — canonical stages are shared
# across all move types now; move_type is only relevant for validating
# Returning Empty (handled by dispatch_stages, not here).
_DIRECT_MAP: dict[str, tuple[str, str]] = {
    "Ready to Dispatch": ("Ready to Dispatch", "Not Started"),
    "Assigned": ("Ready to Dispatch", "Not Started"),
    "Driver Assigned": ("Ready to Dispatch", "Not Started"),
    "Dispatched": ("En Route to Pickup", "Not Started"),
    "En Route to Pickup": ("En Route to Pickup", "Not Started"),
    "At Port": ("At Pickup", "Not Started"),
    "At Pickup": ("At Pickup", "Not Started"),
    "Loaded / Picked Up": ("At Pickup", "Not Started"),
    "Loaded": ("At Pickup", "Not Started"),
    "En Route To Delivery": ("En Route to Delivery", "Not Started"),
    "Delivered": ("Completed", "POD Needed"),
    "Returning Empty": ("Returning Empty", "POD Needed"),
    "POD Received": ("Completed", "POD Received"),
    "Ready for ProfitTools": ("Completed", "Ready for ProfitTools"),
    "Exported to ProfitTools": ("Completed", "Ready for ProfitTools"),
    "Invoiced": ("Completed", "Closed"),
    "Closed": ("Completed", "Closed"),
}


def map_legacy_status(old_status: str, move_type: str) -> tuple[str, str]:
    """Map a legacy loads.status value to (new operational status, closeout_stage).

    move_type is accepted for API stability / future use but the target
    canonical value no longer depends on it — canonical stages are shared
    across move types in the new model. Returns ("", "Not Started") for
    statuses that predate operational dispatch.
    """
    status = (old_status or "").strip()

    if status == "Cancelled":
        return "Cancelled", "Not Started"

    if status in _PRE_DISPATCH_STATUSES:
        return "", "Not Started"

    if status in _DIRECT_MAP:
        return _DIRECT_MAP[status]

    return "", "Not Started"
