from __future__ import annotations

import re


SERVICE_FLOWS = ["Import", "Export", "Local Import", "Local Export"]
SERVICE_FLOW_FILTER_OPTIONS = ["All"] + SERVICE_FLOWS

SERVICE_FLOW_OPTIONS = {
    "all": "All",
    "import": "Import",
    "export": "Export",
    "local_import": "Local Import",
    "local_export": "Local Export",
}

WORKFLOW_STAGES = [
    "Inbox",
    "Order Intake",
    "Booking Review",
    "Ready to Dispatch",
    "Dispatched",
    "Active",
    "Delivered",
    "Ready for Billing",
    "Exported to ProfitTools",
    "Closed",
]

INBOX_QUEUES = [
    "New Order",
    "Existing Load Update",
    "Appointment / PIN",
    "Documents",
    "Billing",
    "Quote Request",
    "Customer Service",
    "Needs Review",
    "Store Only / Archive",
]

ORDER_STATUSES = [
    "New",
    "Missing Info",
    "Booking Verified",
    "Ready to Dispatch",
    "Hold",
    "Cancelled",
]

DISPATCH_STATUSES = [
    "Unassigned",
    "Assigned",
    "Dispatched",
    "Driver En Route",
    "At Port",
    "Loaded",
    "At Warehouse",
    "Delivered",
    "Empty Returned",
    "Completed",
    "Exception",
]

_SERVICE_FLOW_ALIASES = {
    "import": "Import",
    "otr import": "Import",
    "drayage import": "Import",
    "export": "Export",
    "otr export": "Export",
    "drayage export": "Export",
    "local import": "Local Import",
    "import local": "Local Import",
    "local import move": "Local Import",
    "local export": "Local Export",
    "export local": "Local Export",
    "local export move": "Local Export",
}


def _service_flow_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9\s/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_service_flow(value: str, default: str = "") -> str:
    """Map legacy load type values into the canonical service flow labels."""
    key = _service_flow_key(value)
    if not key or key == "local":
        return default

    if key in _SERVICE_FLOW_ALIASES:
        return _SERVICE_FLOW_ALIASES[key]

    is_local = "local" in key
    if "import" in key:
        return "Local Import" if is_local else "Import"
    if "export" in key:
        return "Local Export" if is_local else "Export"

    return default


def service_flow_label(value: str) -> str:
    """Return a display label for a service flow, preserving unknown values."""
    normalized = normalize_service_flow(value)
    return normalized or str(value or "").strip()
