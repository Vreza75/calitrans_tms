from __future__ import annotations

import re
from typing import Any

FIELDS = [
    "TYPE", "Customer", "Booking Number", "Reference Number", "Container Number",
    "Size", "Port", "Warehouse", "Address", "Delivery Need Date",
    "Document Cutoff", "LFD", "Dispatcher Notes",
]

LABEL_ALIASES = {
    "TYPE": ["Order Type", "Type", "Load Type"],
    "Customer": ["Customer", "Company"],
    "Booking Number": ["Booking Number", "Booking #", "Booking", "BKG"],
    "Reference Number": ["Reference Number", "Reference #", "Reference", "Ref", "Ref #"],
    "Container Number": ["Container Number", "Container #", "Container", "Cntr", "Container No"],
    "Size": ["Size", "Container Size"],
    "Port": ["Port", "Terminal", "Port/Terminal", "Pickup", "Pickup Location"],
    "Warehouse": ["Warehouse", "Delivery Warehouse", "Delivery Location", "Deliver To"],
    "Address": ["Address", "Delivery Address", "Warehouse Address"],
    "Delivery Need Date": ["Delivery Need Date", "Delivery Date Needed", "Delivery Date", "Requested Date"],
    "Document Cutoff": ["Document Cutoff", "Cutoff Date", "Doc Cutoff"],
    "LFD": ["LFD", "Last Free Day"],
    "Dispatcher Notes": ["Notes", "Dispatcher Notes", "Instructions", "Special Instructions"],
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().strip("-").strip()


def _find_labeled_value(text: str, aliases: list[str]) -> str:
    for alias in aliases:
        pattern = rf"(?im)^\s*{re.escape(alias)}\s*[:#-]\s*(.+?)\s*$"
        match = re.search(pattern, text)
        if match:
            return _clean(match.group(1))
    return ""


def _infer_type(text: str) -> str:
    lowered = text.lower()
    if "local import" in lowered:
        return "OTR Local Import"
    if "export" in lowered:
        return "OTR Export"
    if "import" in lowered:
        return "OTR Import"
    return ""


def parse_email_text(subject: str | None = None, body: str | None = None) -> dict[str, str]:
    """Parse load order fields from email text.

    Works as parse_email_text(body) or parse_email_text(subject, body).
    """
    if body is None:
        body = subject or ""
        subject = ""

    combined = f"{subject or ''}\n{body or ''}"
    parsed = {field: "" for field in FIELDS}

    for field, aliases in LABEL_ALIASES.items():
        parsed[field] = _find_labeled_value(combined, aliases)

    if not parsed["TYPE"]:
        parsed["TYPE"] = _infer_type(combined)

    if not parsed["Container Number"]:
        match = re.search(r"\b[A-Z]{4}\d{7}\b", combined)
        if match:
            parsed["Container Number"] = match.group(0)

    if not parsed["Booking Number"]:
        match = re.search(r"\b(?:MAEU|ONEY|COSU|ZIMU|HLCU|MSCU)[A-Z0-9-]{4,}\b", combined, re.I)
        if match:
            parsed["Booking Number"] = match.group(0).upper()

    return parsed
