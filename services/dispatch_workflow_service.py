from __future__ import annotations

import re
from datetime import date

import pandas as pd

from services.dispatch_data_service import _read_documents_for_load
from services.workflow_constants import SERVICE_FLOWS, normalize_service_flow, requires_port_pin

LOAD_STATUS_FLOW = [
    "New Email",
    "Needs Review",
    "Order Created",
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Awaiting Appointment",
    "Ready to Dispatch",
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
    "POD Received",
    "Ready for ProfitTools",
    "Exported to ProfitTools",
    "Invoiced",
    "Closed",
    "Cancelled",
]

LOAD_TYPE_TABS = SERVICE_FLOWS

DISPATCH_BOARD_STATUSES = [
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Ready to Dispatch",
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
]

DISPATCH_MOVE_TYPES = LOAD_TYPE_TABS + ["Other"]

DISPATCH_ACTION_WORKFLOWS = {
    "Import": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("sync_port", "Sync Port Data"),
            ("appointment_needed", "Appointment / PIN"),
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Port"),
            ("at_port", "At Port"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Warehouse"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("empty_return", "Empty Return"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Export": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("sync_port", "Sync Booking / Terminal"),
            ("appointment_needed", "Empty / Port Appointment"),
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Empty Yard / Shipper"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Port"),
            ("at_port", "At Port"),
        ],
        "Completion": [
            ("delivered", "Delivered to Port"),
            ("empty_return", "Chassis Return"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Local Import": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Pickup"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Local Export": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("appointment_needed", "Pickup Empty"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Customer"),
            ("at_pickup", "At Customer"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Other": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Pickup"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
}

ACTIVE_DRIVER_STATUSES = [
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
    "Loaded",
    "En Route To Delivery",
    "Returning Empty",
]

CLOSED_STATUSES = ["Closed", "Cancelled", "Invoiced"]

STATUS_UI: dict[str, dict[str, str]] = {
    "New Email":                   {"background": "#f8fafc", "border": "#94a3b8", "text": "#0f172a"},
    "Needs Review":                {"background": "#fef3c7", "border": "#d97706", "text": "#0f172a"},
    "Order Created":                {"background": "#e0f2fe", "border": "#0284c7", "text": "#0f172a"},
    "New":                          {"background": "#f8fafc", "border": "#94a3b8", "text": "#0f172a"},
    "Hold/Need Info":               {"background": "#fecaca", "border": "#dc2626", "text": "#0f172a"},
    "Booking Verified":             {"background": "#dbeafe", "border": "#2563eb", "text": "#0f172a"},
    "Port Verified":                {"background": "#c7d2fe", "border": "#4f46e5", "text": "#0f172a"},
    "Ready for Appointment / PIN":  {"background": "#ddd6fe", "border": "#7c3aed", "text": "#0f172a"},
    "Ready for Port PIN":           {"background": "#ddd6fe", "border": "#7c3aed", "text": "#0f172a"},
    "PIN Received":                 {"background": "#bfdbfe", "border": "#1d4ed8", "text": "#0f172a"},
    "Awaiting Appointment":         {"background": "#fdba74", "border": "#ea580c", "text": "#0f172a"},
    "Ready to Dispatch":            {"background": "#bbf7d0", "border": "#16a34a", "text": "#0f172a"},
    "Driver Assigned":              {"background": "#dcfce7", "border": "#22c55e", "text": "#0f172a"},
    "Assigned":                     {"background": "#dcfce7", "border": "#22c55e", "text": "#0f172a"},
    "Dispatched":                   {"background": "#ccfbf1", "border": "#14b8a6", "text": "#0f172a"},
    "En Route to Pickup":           {"background": "#bef264", "border": "#65a30d", "text": "#0f172a"},
    "At Port":                      {"background": "#fde68a", "border": "#ca8a04", "text": "#0f172a"},
    "At Pickup":                    {"background": "#fde047", "border": "#ca8a04", "text": "#0f172a"},
    "Loaded / Picked Up":           {"background": "#a5b4fc", "border": "#4f46e5", "text": "#0f172a"},
    "Loaded":                       {"background": "#a5b4fc", "border": "#4f46e5", "text": "#0f172a"},
    "En Route To Delivery":         {"background": "#5eead4", "border": "#0d9488", "text": "#0f172a"},
    "En Route to Delivery":         {"background": "#5eead4", "border": "#0d9488", "text": "#0f172a"},
    "At Delivery":                  {"background": "#7dd3fc", "border": "#0284c7", "text": "#0f172a"},
    "Delivered":                    {"background": "#93c5fd", "border": "#2563eb", "text": "#0f172a"},
    "Returning Empty":              {"background": "#e0f2fe", "border": "#0284c7", "text": "#0f172a"},
    "Completed":                    {"background": "#86efac", "border": "#15803d", "text": "#0f172a"},
    "POD Received":                 {"background": "#60a5fa", "border": "#1d4ed8", "text": "#0f172a"},
    "Ready for ProfitTools":        {"background": "#4ade80", "border": "#15803d", "text": "#0f172a"},
    "Exported to ProfitTools":      {"background": "#c4b5fd", "border": "#7c3aed", "text": "#0f172a"},
    "Invoiced":                     {"background": "#f0abfc", "border": "#c026d3", "text": "#0f172a"},
    "Closed":                       {"background": "#d1d5db", "border": "#64748b", "text": "#0f172a"},
    "Cancelled":                    {"background": "#f87171", "border": "#b91c1c", "text": "#0f172a"},
}

_DEFAULT_STATUS_UI = {"background": "#f1f5f9", "border": "#94a3b8", "text": "#0f172a"}


def get_status_ui(status: str) -> dict[str, str]:
    """Single canonical lookup for a status's background/border/text colors.

    Returns a safe neutral default for any status not in STATUS_UI rather
    than raising — callers (badges, cards, lane headers, status history)
    never need their own fallback color.
    """
    return STATUS_UI.get(str(status or "").strip(), _DEFAULT_STATUS_UI)


# Derived view kept for existing callers (ui_components/status_badge.py,
# ui_components/status_legend.py, _status_row_style below) — STATUS_UI is
# the single source of truth now, this dict is not hand-maintained.
STATUS_COLORS = {status: ui["background"] for status, ui in STATUS_UI.items()}

STATUS_MEANINGS = {
    "New Email": "Email received but not converted to order yet",
    "Needs Review": "Needs dispatcher or manager review before order work",
    "Order Created": "Order/load created from intake",
    "New": "New confirmed load, not dispatched yet",
    "Hold/Need Info": "Issue or missing information; dispatcher action required",
    "Booking Verified": "Core order information verified",
    "Port Verified": "Port Houston/container/booking data checked",
    "Ready for Appointment / PIN": "Ready to request port appointment or PIN",
    "Ready for Port PIN": "Ready to request port appointment or PIN",
    "PIN Received": "PIN or gate appointment confirmation is ready",
    "Awaiting Appointment": "Booking confirmed but waiting for pickup or delivery appointment",
    "Ready to Dispatch": "Driver, truck, port, and PIN/appointment are ready for dispatch packet",
    "Driver Assigned": "Driver assigned; confirm truck, PIN/appointment, and packet",
    "Assigned": "Driver and truck assigned",
    "Dispatched": "Driver has been dispatched",
    "En Route to Pickup": "Driver moving toward pickup or terminal",
    "At Port": "Driver is at port or terminal",
    "At Pickup": "Driver checked in or waiting at pickup",
    "Loaded / Picked Up": "Container or freight picked up",
    "Loaded": "Container or freight loaded",
    "En Route To Delivery": "Driver moving toward delivery",
    "Delivered": "Delivered; POD or next billing step needed",
    "Returning Empty": "Driver returning empty container/chassis",
    "POD Received": "Proof of delivery received",
    "Ready for ProfitTools": "Ready for billing/export",
    "Exported to ProfitTools": "Sent to ProfitTools",
    "Invoiced": "Invoice sent",
    "Closed": "Load completed",
    "Cancelled": "Load cancelled",
}

def _safe_str(value, default: str = "") -> str:
    value_str = str(value if value is not None else default).strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return default
    return value_str

def _int_or_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value_text = _safe_str(value)
    if not value_text:
        return None

    try:
        return int(float(value_text))
    except Exception:
        return None

def _first_present(load, keys: list[str], fallback: str = "") -> str:
    for key in keys:
        try:
            value = load.get(key, "")
        except Exception:
            value = ""
        value_str = str(value or "").strip()
        if value_str and value_str.lower() not in {"nan", "none", "nat", "null", "-"}:
            return value_str
    return fallback

def _load_status_rank(status: str) -> int:
    status = _safe_str(status)
    try:
        return LOAD_STATUS_FLOW.index(status)
    except ValueError:
        return -1

def _status_at_or_after(status: str, milestone: str) -> bool:
    status_rank = _load_status_rank(status)
    milestone_rank = _load_status_rank(milestone)
    return status_rank >= milestone_rank >= 0

def _normalize_load_type_value(value: str) -> str:
    return normalize_service_flow(_safe_str(value), default="Other")

def _normalize_load_type(load) -> str:
    return _normalize_load_type_value(_first_present(load, ["TYPE", "type", "Load Type", "load_type"], ""))

def _load_requires_port_type(move_type: str) -> bool:
    return requires_port_pin(move_type)

def _load_requires_port(load) -> bool:
    return _load_requires_port_type(_normalize_load_type(load))

def _load_has_driver(load) -> bool:
    return bool(_first_present(load, ["Driver Name", "driver_name"], ""))

def _load_has_truck(load) -> bool:
    return bool(_first_present(load, ["Truck Assigned", "truck_assigned"], ""))

def _load_has_pin_or_appointment(load) -> bool:
    status = _first_present(load, ["Status", "status"], "")
    if _status_at_or_after(status, "PIN Received"):
        return True
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    return bool(
        _first_present(
            load,
            [
                "pickup_appointment",
                "Pickup Appointment",
                "delivery_appointment",
                "Delivery Appointment",
                "pickup_reference",
                "Pickup Reference",
                "delivery_reference",
                "Delivery Reference",
            ],
            "",
        )
        or "pin received" in notes
        or "appointment confirmation" in notes
        or "express pass" in notes
    )

def _load_port_verified(load) -> bool:
    status = _first_present(load, ["Status", "status"], "")
    if _status_at_or_after(status, "Port Verified"):
        return True
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    return bool(
        _first_present(load, ["terminal", "Terminal", "empty_return_location", "Empty Return Location", "current_location"], "")
        or "port houston evp update" in notes
        or "container lookup complete" in notes
        or "booking lookup complete" in notes
    )

def _load_document_count(load_id: int | None, documents_df: pd.DataFrame | None = None) -> int:
    if documents_df is not None:
        return len(documents_df)
    if load_id is None:
        return 0
    return len(_read_documents_for_load(int(load_id)))

def _load_readiness_details(load, documents_df: pd.DataFrame | None = None, include_documents: bool = True) -> dict:
    status = _first_present(load, ["Status", "status"], "New")
    move_type = _normalize_load_type(load)
    requires_port = _load_requires_port_type(move_type)
    load_id = _int_or_none(load.get("_row_id") if hasattr(load, "get") else None)
    has_docs = _load_document_count(load_id, documents_df) > 0 if include_documents else True
    has_driver = _load_has_driver(load)
    has_truck = _load_has_truck(load)
    has_pin = _load_has_pin_or_appointment(load)
    port_verified = _load_port_verified(load) if requires_port else True

    checks = [
        ("Customer", bool(_first_present(load, ["Customer", "customer"], ""))),
        ("Order / booking #", bool(_first_present(load, ["Booking Number", "booking_number", "Reference Number", "Load ID"], ""))),
        ("Container #", bool(_first_present(load, ["Container Number", "container_number"], ""))),
        ("Warehouse", bool(_first_present(load, ["Warehouse", "warehouse"], ""))),
        ("Delivery need date", bool(_first_present(load, ["Delivery Need Date", "delivery_need_date"], ""))),
        ("Size", bool(_first_present(load, ["Size", "size"], ""))),
        ("Service Flow", bool(_first_present(load, ["TYPE", "type"], ""))),
    ]
    if requires_port:
        checks.extend(
            [
                ("Steamship line", bool(_first_present(load, ["steamship_line", "Steamship Line"], ""))),
                ("Port / terminal", bool(_first_present(load, ["Port", "port", "terminal", "Terminal"], ""))),
            ]
        )
    if include_documents:
        checks.append(("Documents attached", has_docs))
    if requires_port:
        checks.append(("Port verified", port_verified))
    checks.extend([("Driver assigned", has_driver), ("Truck assigned", has_truck)])
    if requires_port:
        checks.append(("PIN / appointment", has_pin))

    missing = [label for label, is_ready in checks if not is_ready]
    completed = len(checks) - len(missing)
    score = int(round((completed / len(checks)) * 100)) if checks else 0

    exceptions = []
    lfd_date = pd.to_datetime(_first_present(load, ["LFD", "lfd"], ""), errors="coerce")
    delivery_date = pd.to_datetime(_first_present(load, ["Delivery Need Date", "delivery_need_date"], ""), errors="coerce")
    today = pd.Timestamp(date.today()).normalize()
    if pd.notna(lfd_date) and lfd_date.normalize() <= today and status not in ["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"]:
        exceptions.append("LFD today")
    if pd.notna(delivery_date) and delivery_date.normalize() < today and status not in ["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"]:
        exceptions.append("Late appointment")
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"] and not has_driver:
        exceptions.append("No driver assigned")
    if requires_port and status in ["Ready for Appointment / PIN", "Ready for Port PIN", "Ready to Dispatch", "Driver Assigned", "Assigned"] and not has_pin:
        exceptions.append("No PIN / appointment")
    if status in ["Delivered"] and not has_docs:
        exceptions.append("No POD")
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    if requires_port and any(term in notes for term in ["hold", "customs hold", "line hold", "exam", "x-ray"]):
        exceptions.append("Container hold")

    verification_missing = [
        "Customer",
        "Order / booking #",
        "Container #",
        "Warehouse",
        "Delivery need date",
        "Size",
        "Service Flow",
    ]
    if requires_port:
        verification_missing.extend(["Steamship line", "Port / terminal"])

    if any(item in missing for item in verification_missing):
        next_action = "Complete missing order details"
    elif "Documents attached" in missing:
        next_action = "Attach load documents"
    elif not _status_at_or_after(status, "Booking Verified"):
        next_action = "Verify booking"
    elif requires_port and not port_verified:
        next_action = "Verify booking with Port Houston"
    elif not has_driver or not has_truck:
        next_action = "Assign driver and truck"
    elif requires_port and not has_pin:
        next_action = "Request PIN / appointment"
    elif status in ["PIN Received", "Driver Assigned", "Assigned", "Ready to Dispatch"]:
        next_action = "Send dispatch packet"
    elif status == "Delivered":
        next_action = "Upload POD"
    elif status in ["POD Received", "Ready for ProfitTools"]:
        next_action = "Move to billing"
    else:
        next_action = _next_status_goal(status)

    return {
        "score": score,
        "missing": missing,
        "next_action": next_action,
        "exceptions": exceptions,
        "port_verified": port_verified,
        "pin_ready": has_pin,
        "requires_port": requires_port,
        "move_type": move_type,
        "dispatchable": not missing and port_verified and has_driver and has_truck and (has_pin or not requires_port),
    }

def _readiness_label(details: dict) -> str:
    missing = details.get("missing") or []
    if not missing:
        return f"{details.get('score', 0)}% Ready"
    return f"{details.get('score', 0)}% Ready - Missing: {', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}"

def _load_department_queue(load) -> str:
    status = _first_present(load, ["Status", "status"], "New")
    readiness = _load_readiness_details(load, include_documents=False)
    exceptions = readiness.get("exceptions") or []
    if status in ["POD Received", "Ready for ProfitTools"]:
        return "Accounting - Ready for ProfitTools"
    if status in ["Invoiced", "Closed"]:
        return "Accounting - Closed / Invoiced"
    if any(term in _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower() for term in ["detention", "demurrage", "invoice", "billing"]):
        return "Accounting - Detention / Demurrage"
    if not _load_has_driver(load) and status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch"]:
        return "Manager - Unassigned"
    if exceptions:
        return "Manager - Critical / Exceptions"
    if status in ["New", "Order Created", "Needs Review"]:
        return "Dispatcher - New Orders"
    if status in ["Hold/Need Info"]:
        return "Dispatcher - Need Info"
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN"]:
        return "Dispatcher - Ready for PIN"
    if status in ["PIN Received", "Ready to Dispatch"]:
        return "Dispatcher - Ready to Dispatch"
    if status in ACTIVE_DRIVER_STATUSES or status in ["At Port", "Loaded / Picked Up", "Delivered"]:
        return "Dispatcher - Active Loads"
    return "Dispatcher - Exceptions"

def _load_exception_summary(df: pd.DataFrame) -> dict[str, int]:
    work_df = df.copy()
    if work_df.empty or "Status" not in work_df.columns:
        return {}
    open_df = work_df[~work_df["Status"].isin(CLOSED_STATUSES)].copy()
    open_df["Dispatch Move Type"] = open_df.get("TYPE", pd.Series("", index=open_df.index)).apply(_normalize_load_type_value)
    port_required = open_df["Dispatch Move Type"].isin(["Import", "Export"])
    today = pd.Timestamp(date.today()).normalize()
    lfd_dates = pd.to_datetime(open_df.get("LFD", ""), errors="coerce")
    delivery_dates = pd.to_datetime(open_df.get("Delivery Need Date", ""), errors="coerce")
    no_driver_statuses = ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch"]
    notes = open_df.get("Dispatcher Notes", pd.Series("", index=open_df.index)).fillna("").astype(str).str.lower()
    no_driver_mask = open_df["Status"].isin(no_driver_statuses) & open_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])
    no_pin_mask = (
        port_required
        & open_df["Status"].isin(["Ready for Appointment / PIN", "Ready for Port PIN", "Ready to Dispatch", "Driver Assigned", "Assigned"])
        & ~open_df.apply(_load_has_pin_or_appointment, axis=1)
    )
    port_hold_mask = port_required & notes.str.contains("customs hold|line hold|x-ray|exam|hold", regex=True, na=False)
    return {
        "LFD today": int((lfd_dates.notna() & lfd_dates.dt.normalize().le(today) & ~open_df["Status"].isin(["Delivered", "POD Received", "Ready for ProfitTools"])).sum()),
        "Late appointment": int((delivery_dates.notna() & delivery_dates.dt.normalize().lt(today) & ~open_df["Status"].isin(["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"])).sum()),
        "No driver assigned": int(no_driver_mask.sum()),
        "Waiting driver": int(no_driver_mask.sum()),
        "No PIN": int(no_pin_mask.sum()),
        "Appointment missing": int((port_required & open_df["Status"].isin(["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN"]) & ~open_df.apply(_load_has_pin_or_appointment, axis=1)).sum()),
        "Customer waiting": int(open_df["Status"].eq("Hold/Need Info").sum()),
        "Container hold": int(port_hold_mask.sum()),
        "Port hold": int(port_hold_mask.sum()),
        "No POD": int(open_df["Status"].eq("Delivered").sum()),
        "Ready for billing": int(open_df["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum()),
    }

def _dispatch_workflow_for_type(move_type: str) -> dict:
    normalized_type = _normalize_load_type_value(move_type)
    return DISPATCH_ACTION_WORKFLOWS.get(normalized_type, DISPATCH_ACTION_WORKFLOWS["Other"])

def _dispatch_action_labels(move_type: str) -> dict[str, tuple[str, str, int, int]]:
    labels = {}
    workflow = _dispatch_workflow_for_type(move_type)
    for lane_idx, (lane_name, actions) in enumerate(workflow.items()):
        for action_idx, (action_key, action_label) in enumerate(actions):
            labels[action_key] = (lane_name, action_label, lane_idx, action_idx)
    return labels

def _dispatch_action_metadata(load, readiness: dict | None = None) -> dict:
    status = _first_present(load, ["Status", "status"], "New")
    move_type = _normalize_load_type(load)
    requires_port = _load_requires_port_type(move_type)
    readiness = readiness or _load_readiness_details(load, include_documents=False)
    labels = _dispatch_action_labels(move_type)

    def choose(action_key: str, fallback_lane: str, hint: str) -> dict:
        if action_key not in labels:
            workflow = _dispatch_workflow_for_type(move_type)
            fallback_actions = workflow.get(fallback_lane) or next(iter(workflow.values()))
            action_key = fallback_actions[0][0]
        lane_name, action_label, lane_idx, action_idx = labels[action_key]
        return {
            "lane": lane_name,
            "action": action_key,
            "label": action_label,
            "hint": hint,
            "lane_sort": lane_idx,
            "action_sort": action_idx,
        }

    missing = set(readiness.get("missing") or [])
    verification_missing = missing - {"Documents attached", "Port verified", "Driver assigned", "Truck assigned", "PIN / appointment"}
    has_driver = _load_has_driver(load)
    has_truck = _load_has_truck(load)
    has_pin = _load_has_pin_or_appointment(load)
    port_verified = bool(readiness.get("port_verified", True))

    if status in ["Closed", "Invoiced", "Exported to ProfitTools"]:
        return choose("completed", "Completion", "Load complete or exported.")
    if status in ["Cancelled"]:
        return choose("completed", "Completion", "Cancelled load.")
    if status in ["POD Received", "Ready for ProfitTools"]:
        return choose("ready_billing", "Completion", "Move to billing / ProfitTools.")
    if status == "Delivered":
        return choose("delivered", "Completion", "Collect POD and clear billing handoff.")
    if status == "Returning Empty":
        return choose("empty_return", "Completion", "Confirm empty or chassis return.")

    if status in ["New Email", "Order Created", "New"]:
        return choose("new_orders", "Verification", "Review order and confirm core details.")
    if status in ["Needs Review", "Hold/Need Info"] or verification_missing:
        return choose("needs_verification", "Verification", "Fix missing or questionable load details.")
    if "Documents attached" in missing:
        return choose("documents", "Verification", "Attach or review required load documents.")

    if requires_port and not port_verified:
        return choose("sync_port", "Planning", "Sync Port Houston data before dispatch.")
    if not has_driver or not has_truck:
        return choose("assign_driver", "Planning", "Assign driver, truck, and chassis.")
    if requires_port and not has_pin:
        return choose("appointment_needed", "Planning", "Book appointment or request PIN before dispatch.")
    if move_type == "Local Export" and status in ["Booking Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"]:
        return choose("appointment_needed", "Planning", "Confirm empty pickup before sending the final packet.")
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"]:
        return choose("send_packet", "Planning", "Send dispatch packet when load is ready.")

    if status in ["Dispatched", "En Route to Pickup"]:
        return choose("enroute_pickup", "Execution", "Track movement toward pickup.")
    if status == "At Port":
        return choose("at_port", "Execution", "Track terminal activity.")
    if status == "At Pickup":
        return choose("at_pickup", "Execution", "Track pickup / customer arrival.")
    if status in ["Loaded / Picked Up", "Loaded"]:
        return choose("loaded", "Execution", "Track load after pickup.")
    if status == "En Route To Delivery":
        return choose("enroute_delivery", "Execution", "Track delivery ETA.")

    return choose("needs_verification", "Verification", "Review load status and next step.")

def _next_status_goal(new_status: str) -> str:
    flow = LOAD_STATUS_FLOW
    if new_status in flow:
        idx = flow.index(new_status)
        if idx + 1 < len(flow):
            return flow[idx + 1]
    return "Next dispatch milestone"

def _eta_to_next_goal(load, new_status: str) -> str:
    eta_value = _first_present(load, ["eta", "ETA"], "")
    if eta_value:
        return eta_value

    if new_status in ["Assigned", "En Route to Pickup", "At Pickup"]:
        eta_value = _first_present(load, ["pickup_appointment", "Pickup Appointment", "Delivery Need Date"], "")
        if eta_value:
            return eta_value

    if new_status in ["Loaded", "En Route To Delivery", "Delivered"]:
        eta_value = _first_present(load, ["delivery_appointment", "Delivery Appointment", "Delivery Need Date"], "")
        if eta_value:
            return eta_value

    if new_status == "Returning Empty":
        eta_value = _first_present(load, ["empty_return_date", "Empty Return Date", "LFD"], "")
        if eta_value:
            return eta_value

    return "ETA pending dispatch update"

def _load_pin_display(load) -> str:
    return _first_present(
        load,
        [
            "pickup_reference",
            "Pickup Reference",
            "delivery_reference",
            "Delivery Reference",
            "pickup_appointment",
            "Pickup Appointment",
            "delivery_appointment",
            "Delivery Appointment",
        ],
        "-",
    )

def _clean_display_value(value, fallback: str = "-") -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", ""}:
        return fallback
    return value_str

def _generate_driver_dispatch_message(selected_load) -> str:
    booking = _clean_display_value(selected_load.get("Booking Number", ""))
    container = _clean_display_value(selected_load.get("Container Number", ""))
    customer = _clean_display_value(selected_load.get("Customer", ""))
    pickup = _clean_display_value(selected_load.get("Port", "") or selected_load.get("terminal", ""))
    terminal = _clean_display_value(selected_load.get("terminal", "") or selected_load.get("Port", ""))
    delivery = _clean_display_value(selected_load.get("Warehouse", ""))
    address = _clean_display_value(selected_load.get("Address", ""))
    delivery_need = _clean_display_value(selected_load.get("Delivery Need Date", ""))
    lfd = _clean_display_value(selected_load.get("LFD", ""))
    chassis = _clean_display_value(selected_load.get("Chassis", ""))
    chassis_provider = _clean_display_value(selected_load.get("chassis_provider", ""), "")
    size = _clean_display_value(selected_load.get("Size", ""))
    pickup_appt = _clean_display_value(selected_load.get("pickup_appointment", ""), "")
    delivery_appt = _clean_display_value(selected_load.get("delivery_appointment", ""), "")
    empty_return = _clean_display_value(selected_load.get("empty_return_location", ""), "")
    empty_return_date = _clean_display_value(selected_load.get("empty_return_date", ""), "")
    pin_or_appt = _load_pin_display(selected_load)
    notes = _clean_display_value(selected_load.get("Dispatcher Notes", ""), "")

    message = f"""LOAD ASSIGNMENT

Booking: {booking}
Container: {container}
Customer: {customer}
Size: {size}

TERMINAL / PICKUP
Terminal: {terminal}
Pickup Location: {pickup}
PIN / Appointment: {pin_or_appt}
Pickup Appointment: {pickup_appt or "-"}

DELIVERY
Warehouse: {delivery}
Address: {address}
Delivery Appointment: {delivery_appt or "-"}

Delivery Need Date: {delivery_need}
LFD: {lfd}
Chassis: {chassis}
Chassis Provider: {chassis_provider or "-"}
Empty Return: {empty_return or "-"}
Empty Return Date: {empty_return_date or "-"}

Instructions:
{notes if notes else "Please confirm when en route, at pickup, loaded, delivered, and empty returned."}
"""
    return message.strip()

def _get_status_color(status: str) -> str:
    return get_status_ui(status)["background"]

def _get_status_border_color(status: str) -> str:
    return get_status_ui(status)["border"]

def _status_row_style(row):
    status = str(row.get("Status", ""))
    color = STATUS_COLORS.get(status, "#ffffff")
    return [f"background-color: {color}"] * len(row)
