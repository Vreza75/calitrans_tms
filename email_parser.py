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


def _append_note(existing: str, note: str) -> str:
    existing = _clean(existing)
    note = _clean(note)
    if not note:
        return existing
    return note if not existing else f"{existing}; {note}"


def _first_container(text: str) -> str:
    match = re.search(r"\b[A-Z]{4}\d{7}\b", text, re.I)
    return match.group(0).upper() if match else ""


def _container_correction(text: str) -> tuple[str, str]:
    match = re.search(
        r"\bcontainer\s+([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()

    match = re.search(
        r"\b([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()

    return "", ""


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

    corrected_container, replaced_container = _container_correction(combined)
    if corrected_container:
        parsed["Container Number"] = corrected_container
        parsed["Dispatcher Notes"] = _append_note(
            parsed["Dispatcher Notes"],
            f"Container correction noted: use {corrected_container} instead of {replaced_container}.",
        )

    subject_pair = re.search(r"\b(\d{5,})\s*/\s*([A-Z]{4}\d{7})\b", subject or "", re.I)
    if subject_pair:
        if not parsed["Reference Number"]:
            parsed["Reference Number"] = subject_pair.group(1)
        if not parsed["Container Number"]:
            parsed["Container Number"] = subject_pair.group(2).upper()

    if not parsed["Container Number"]:
        parsed["Container Number"] = _first_container(combined)

    if not parsed["Booking Number"]:
        match = re.search(r"\b(?:MAEU|ONEY|COSU|ZIMU|HLCU|MSCU)[A-Z0-9-]{4,}\b", combined, re.I)
        if match:
            parsed["Booking Number"] = match.group(0).upper()

    if not parsed["Reference Number"]:
        match = (
            re.search(r"\b(?:ref(?:erence)?|po|order|load)\s*(?:#|number|no\.?)?\s*[:#-]?\s*([0-9][0-9\s-]{4,})\b", combined, re.I)
            or re.search(r"/\s*([0-9][0-9\s-]{3,})\b", subject or "")
            or re.search(r"\b([0-9]{6,})\s*/\s*[A-Z]{4}\d{7}\b", subject or "", re.I)
        )
        if match:
            parsed["Reference Number"] = re.sub(r"\s+", " ", match.group(1)).strip()

    if not parsed["Customer"]:
        if re.search(r"\bflat\s*world\b|@flatworldgs\.com\b", combined, re.I):
            parsed["Customer"] = "Flat World Global Logistics"

    if not parsed["Size"]:
        match = re.search(r"\b(?:\d+\s*x\s*)?(20|40|45)\s*'?\s*(?:ft|hc|hq|std|drayage)?\b", combined, re.I)
        if match:
            parsed["Size"] = match.group(1)

    if not parsed["Delivery Need Date"]:
        match = (
            re.search(r"\bready\s+for\s+loading\s+on\s+(?:the\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", combined, re.I)
            or re.search(r"\bready\s+(?:on|for)\s+(?:the\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", combined, re.I)
        )
        if match:
            parsed["Delivery Need Date"] = match.group(1)

    if not parsed["Document Cutoff"]:
        match = re.search(r"\b(?:doc(?:ument)?\s*)?cut\s*off|cutoff\b", combined, re.I)
        if match:
            date_match = re.search(r"\b(?:doc(?:ument)?\s*)?(?:cut\s*off|cutoff)\s*(?:is|:)?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", combined, re.I)
            if date_match:
                parsed["Document Cutoff"] = date_match.group(1)

    lowered = combined.lower()
    if "delivery order" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Delivery order mentioned in email")
    if "packing list" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Packing list mentioned in email")
    if "hazmat" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Hazmat mentioned in email")
    if "imo" in lowered or "imos" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "IMO documents mentioned in email")

    return parsed
