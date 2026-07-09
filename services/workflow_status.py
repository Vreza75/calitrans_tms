from __future__ import annotations

from typing import Any

import pandas as pd

from services.workflow_constants import normalize_service_flow


ORDER_STATUS_VALUES = ["new", "missing_info", "booking_verified", "ready_to_dispatch", "on_hold", "cancelled"]
DISPATCH_STATUS_VALUES = [
    "unassigned",
    "assigned",
    "dispatched",
    "driver_en_route",
    "at_port",
    "loaded",
    "at_warehouse",
    "delivered",
    "empty_returned",
    "completed",
    "exception",
]
DOCUMENT_STATUS_VALUES = ["not_started", "received", "parsed", "needs_review", "approved", "attached_to_load", "complete"]
BILLING_STATUS_VALUES = ["not_ready", "missing_docs", "rate_review", "ready_for_profittools", "exported_to_profittools", "closed"]
PIN_STATUS_VALUES = ["not_needed", "needed", "requested", "received", "sent_to_driver", "failed", "expired"]


def _safe_str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    if text.lower() in {"nan", "none", "nat", "null"}:
        return default
    return text


def _first_present(row: Any, keys: list[str], default: str = "") -> str:
    for key in keys:
        value = row.get(key, "") if hasattr(row, "get") else ""
        text = _safe_str(value)
        if text:
            return text
    return default


def service_flow_code(value: Any) -> str:
    normalized = normalize_service_flow(_safe_str(value), default="")
    return normalized.lower().replace(" ", "_") if normalized else ""


def _status(row: Any) -> str:
    return _first_present(row, ["Status", "status"], "New")


def _has_assignment(row: Any) -> bool:
    driver = _first_present(row, ["Driver Name", "driver_name"])
    truck = _first_present(row, ["Truck Assigned", "truck_assigned"])
    return bool(driver or truck)


def _has_pin_or_appointment(row: Any) -> bool:
    status = _status(row).lower()
    if "pin received" in status:
        return True

    for key in ["pickup_reference", "delivery_reference", "pickup_appointment", "delivery_appointment"]:
        if _first_present(row, [key]):
            return True

    notes = _first_present(row, ["Dispatcher Notes", "dispatcher_notes"]).lower()
    return "pin" in notes or "appointment confirmation" in notes


def derive_workflow_stage(row: Any) -> str:
    status = _status(row)
    if status in {"New Email", "Needs Review"}:
        return "Inbox"
    if status in {"Order Created", "New"}:
        return "Order Intake"
    if status in {"Hold/Need Info", "Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "Awaiting Appointment"}:
        return "Booking Review"
    if status in {"PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"}:
        return "Ready to Dispatch"
    if status == "Dispatched":
        return "Dispatched"
    if status in {"En Route to Pickup", "At Port", "At Pickup", "Loaded / Picked Up", "Loaded", "En Route To Delivery", "Returning Empty"}:
        return "Active"
    if status in {"Delivered", "POD Received"}:
        return "Delivered"
    if status == "Ready for ProfitTools":
        return "Ready for Billing"
    if status == "Exported to ProfitTools":
        return "Exported to ProfitTools"
    if status in {"Invoiced", "Closed", "Cancelled"}:
        return "Closed"
    return "Order Intake"


def derive_order_status(row: Any) -> str:
    status = _status(row)
    if status == "Cancelled":
        return "cancelled"
    if status == "Hold/Need Info":
        return "missing_info"
    if status in {"New Email", "Needs Review", "Order Created", "New"}:
        return "new"
    if status in {"Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "Awaiting Appointment", "PIN Received"}:
        return "booking_verified"
    return "ready_to_dispatch"


def derive_dispatch_status(row: Any) -> str:
    status = _status(row)
    if status == "Cancelled":
        return "exception"
    if status in {"Invoiced", "Closed"}:
        return "completed"
    if status in {"Exported to ProfitTools", "Ready for ProfitTools"}:
        return "completed"
    if status in {"Delivered", "POD Received"}:
        return "delivered"
    if status == "Returning Empty":
        return "empty_returned"
    if status in {"Loaded / Picked Up", "Loaded"}:
        return "loaded"
    if status == "At Port":
        return "at_port"
    if status == "At Pickup":
        return "at_warehouse"
    if status in {"En Route to Pickup", "En Route To Delivery"}:
        return "driver_en_route"
    if status == "Dispatched":
        return "dispatched"
    if status in {"Driver Assigned", "Assigned"}:
        return "assigned"
    return "assigned" if _has_assignment(row) else "unassigned"


def derive_document_status(row: Any) -> str:
    status = _status(row)
    if status in {"POD Received", "Ready for ProfitTools", "Exported to ProfitTools", "Invoiced", "Closed"}:
        return "complete"
    if status == "Delivered":
        return "received"
    notes = _first_present(row, ["Dispatcher Notes", "dispatcher_notes"]).lower()
    if "document" in notes or "pod" in notes or "bol" in notes:
        return "attached_to_load"
    return "not_started"


def derive_billing_status(row: Any) -> str:
    status = _status(row)
    invoice_status = _first_present(row, ["invoice_status", "Invoice Status"]).lower()
    ready_flag = _first_present(row, ["Ready for ProfitTools", "ready_for_profittools"]).lower() in {"true", "yes", "1", "ready"}

    if status in {"Invoiced", "Closed"}:
        return "closed"
    if status == "Exported to ProfitTools" or "export" in invoice_status:
        return "exported_to_profittools"
    if status == "Ready for ProfitTools" or ready_flag or "ready" in invoice_status:
        return "ready_for_profittools"
    if status == "POD Received":
        return "rate_review"
    if status == "Delivered":
        return "missing_docs"
    return "not_ready"


def derive_pin_status(row: Any) -> str:
    flow = service_flow_code(_first_present(row, ["TYPE", "type", "Service Flow", "service_flow"]))
    if flow not in {"import", "export"}:
        return "not_needed"

    status = _status(row)
    has_pin = _has_pin_or_appointment(row)
    if status in {"Dispatched", "En Route to Pickup", "At Port", "Loaded / Picked Up", "Loaded", "En Route To Delivery", "Delivered"} and has_pin:
        return "sent_to_driver"
    if has_pin or status == "PIN Received":
        return "received"
    if status in {"Ready for Appointment / PIN", "Ready for Port PIN", "Awaiting Appointment"}:
        return "requested"
    if status in {"Booking Verified", "Port Verified", "Ready to Dispatch", "Driver Assigned", "Assigned"}:
        return "needed"
    return "not_needed"


def derive_load_workflow_fields(row: Any) -> dict[str, str]:
    service_flow_display = normalize_service_flow(_first_present(row, ["TYPE", "type", "Service Flow", "service_flow"]), default="")
    return {
        "service_flow": service_flow_code(service_flow_display),
        "Service Flow": service_flow_display,
        "workflow_stage": derive_workflow_stage(row),
        "order_status": derive_order_status(row),
        "dispatch_status": derive_dispatch_status(row),
        "document_status": derive_document_status(row),
        "billing_status": derive_billing_status(row),
        "pin_status": derive_pin_status(row),
    }


def enrich_load_workflow_columns(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    workflow_columns = [
        "service_flow",
        "Service Flow",
        "workflow_stage",
        "order_status",
        "dispatch_status",
        "document_status",
        "billing_status",
        "pin_status",
    ]
    if enriched.empty:
        for column in workflow_columns:
            enriched[column] = pd.Series(dtype="object")
        return enriched

    workflow_fields = enriched.apply(derive_load_workflow_fields, axis=1, result_type="expand")
    for column in workflow_columns:
        enriched[column] = workflow_fields[column].fillna("")
    return enriched
