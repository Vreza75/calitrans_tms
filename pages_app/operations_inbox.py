import json
import logging
import math
import re
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import services.operations_inbox_service as ops

from uuid import uuid4
from services.email_parser import extract_latest_email_body
from services.operations_field_service import reconcile_parsed_sources, validate_field_value
from services.operations_multi_container_service import create_container_work_orders
from services.operations_open_timing import WorkItemOpenTiming
from services.workflow_constants import normalize_service_flow
from config import get_config_source
from ui_components.dialog_presets import DIALOG_SIZE_PRESETS
from ui_components.flow_filters import apply_service_flow_filter, extract_service_flow_value, render_service_flow_filter
from ui_components.operations_inbox_state import (
    WORK_ITEM_DIALOG_OPEN,
    WORK_ITEM_OPEN_STARTED_AT,
    close_work_item,
    initialize_work_item_state,
    open_work_item,
    should_show_contextual_load_match as _should_show_contextual_load_match,
)

# Temporary compatibility aliases while Operations Inbox is extracted from app.py.
REQUEST_TYPES = ops.REQUEST_TYPES
REPLY_LANGUAGE_OPTIONS = ops.REPLY_LANGUAGE_OPTIONS
REPLY_TONE_OPTIONS = ops.REPLY_TONE_OPTIONS
OPERATIONS_CONTROL_LEVEL_DESCRIPTIONS = ops.OPERATIONS_CONTROL_LEVEL_DESCRIPTIONS
OPERATIONS_CONTROL_LEVELS = ops.OPERATIONS_CONTROL_LEVELS
logger = logging.getLogger(__name__)


_safe_str = ops._safe_str
_coerce_json_dict = ops._coerce_json_dict
_effective_operations_request_type_for_row = ops._effective_operations_request_type_for_row
_operations_owner_label_for_row = ops._operations_owner_label_for_row
_operations_priority_label_for_row = ops._operations_priority_label_for_row
_operations_status_label_for_row = ops._operations_status_label_for_row
_operations_work_queue_for_row = ops._operations_work_queue_for_row
_operations_control_level_for_row = ops._operations_control_level_for_row
_operations_department_lane_for_row = ops._operations_department_lane_for_row
_operations_work_item_for_row = ops._operations_work_item_for_row
_operations_recommended_action_for_row = ops._operations_recommended_action_for_row
_operations_queue_labels_for_level = ops._operations_queue_labels_for_level
_operations_queue_mask_for_level = ops._operations_queue_mask_for_level
_load_operations_inbox_record = ops._load_operations_inbox_record
_row_conversation_join_key = ops._row_conversation_join_key
_store_operations_parsed_data = ops._store_operations_parsed_data
_conversation_key_from_candidate = ops._conversation_key_from_candidate
_resolve_reply_language = ops._resolve_reply_language
_action_required_for_request = ops._action_required_for_request
_detect_customer_language = ops._detect_customer_language
_extract_email_address = ops._extract_email_address
_render_operations_case_panel = ops._render_operations_case_panel
_render_operations_attachment_preview = ops._render_operations_attachment_preview
_render_operations_pdf_panel = ops._render_operations_pdf_panel

# ---------------------------------------------------------------------
# Dispatcher-first UI helpers
# ---------------------------------------------------------------------

def _ops_blank(value, fallback="-") -> str:
    text = ops._safe_str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return fallback
    return text


def _ops_sender_name(sender: str) -> str:
    sender = ops._safe_str(sender).strip()
    if not sender:
        return "-"
    if "<" in sender:
        return sender.split("<", 1)[0].strip().strip('"') or sender
    if "@" in sender:
        return sender.split("@", 1)[0].replace(".", " ").replace("_", " ").title()
    return sender


def _ops_compact_subject(subject: str, max_len: int = 90) -> str:
    subject = ops._safe_str(subject).strip()
    if len(subject) <= max_len:
        return subject
    return subject[: max_len - 3].rstrip() + "..."


def _ops_is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    text = ops._safe_str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "required", "needed"}


def _ops_text_blob(*values) -> str:
    return " ".join(
        ops._safe_str(value)
        for value in values
        if value is not None
    ).lower()

def _complete_operations_email_action(
    *,
    selected_id: int,
    mark_waiting: bool,
    reply_to: str,
    reply_subject: str,
) -> None:
    """
    Mark the selected inbox item as actioned and clear UI selection.

    If mark_waiting is true, the email moves to Waiting on Customer.
    Otherwise, it is closed as action completed.
    """

    if mark_waiting:
        review_status = "Waiting on Customer"
        action_required = "Reply sent; waiting on customer response."
    else:
        review_status = "Closed"
        action_required = "Reply sent; no further action required."

    ops._execute(
        """
        update order_intake
        set review_status = :review_status,
            action_required = :action_required
        where id = :intake_id
        """,
        {
            "intake_id": int(selected_id),
            "review_status": review_status,
            "action_required": action_required,
        },
    )

    st.session_state["operations_last_completed_action"] = {
        "intake_id": int(selected_id),
        "status": review_status,
        "recipient": reply_to,
        "subject": reply_subject,
    }

    close_work_item(st.session_state)
    # Clear selected action and reply widgets for this email.
    selected_id_text = str(selected_id)

    for key in list(st.session_state.keys()):
        if selected_id_text in key and key.startswith(
            (
                "operations_selected_primary_action_",
                "operations_action_mode_",
                "operations_reply_from_",
                "operations_reply_to_",
                "operations_reply_cc_",
                "operations_reply_subject_",
                "operations_reply_body_",
                "operations_reply_waiting_",
            )
        ):
            st.session_state.pop(key, None)
def _ops_has_reply_or_update_signal(subject: str, body: str, record=None) -> bool:
    text = _ops_text_blob(subject, body)
    subject_text = ops._safe_str(subject).strip().lower()

    update_terms = [
        "thank you for the update",
        "thanks for the update",
        "received your update",
        "booking update",
        "booking revision",
        "revised booking",
        "roll this booking",
        "i will roll",
        "will roll",
        "rolled",
        "updated file",
        "share as soon as i receive",
        "as soon as i receive the updated file",
        "new cut",
        "next cut",
        "cut is",
        "cutoff",
        "eta changed",
        "appointment changed",
        "appt changed",
        "rescheduled",
        "status update",
        "any update",
        "please advise status",
    ]

    if any(term in text for term in update_terms):
        return True

    if subject_text.startswith(("re:", "fw:", "fwd:")):
        reply_terms = [
            "thank you",
            "thanks",
            "received",
            "will follow up",
            "i will send",
            "i will share",
            "updated file",
        ]
        if any(term in text for term in reply_terms):
            return True

    if record is not None:
        request_type = ops._safe_str(record.get("request_type_clean") or record.get("request_type")).lower()
        dispatcher_queue = ops._safe_str(record.get("dispatcher_queue")).lower()

        if request_type in {
            "booking update",
            "appointment update",
            "port issue",
            "customer request",
            "missing information",
            "cancellation",
            "driver issue",
        }:
            return True

    return False


def _ops_has_true_new_order_signal(subject: str, body: str, record=None) -> bool:
    text = _ops_text_blob(subject, body)
    subject_text = ops._safe_str(subject).strip().lower()

    if subject_text.startswith(("re:", "fw:", "fwd:")) and _ops_has_reply_or_update_signal(subject, body, record):
        return False

    new_order_terms = [
        "new booking",
        "new order",
        "please book",
        "please schedule",
        "please arrange",
        "please pick up",
        "please deliver",
        "delivery order",
        "drayage order",
        "work order",
        "container move",
        "move request",
    ]

    if any(term in text for term in new_order_terms):
        return True

    if record is not None:
        request_type = ops._safe_str(record.get("request_type_clean") or record.get("request_type"))
        if request_type == "New Booking" and not _ops_has_reply_or_update_signal(subject, body, record):
            return True

    return False


def _ops_has_document_signal(record, parsed=None, subject: str = "", body: str = "") -> bool:
    parsed = parsed if isinstance(parsed, dict) else {}
    text = _ops_text_blob(subject, body, record.get("request_type", "") if record is not None else "")

    attachment_count = 0
    if record is not None:
        try:
            attachment_count = int(record.get("attachment_count") or record.get("source_attachment_count") or 0)
        except Exception:
            attachment_count = 0

    document_terms = [
        "pod",
        "proof of delivery",
        "bol",
        "bill of lading",
        "invoice",
        "rate confirmation",
        "delivery order",
        "attached",
        "attachment",
        "document",
        "pdf",
    ]

    return bool(
        attachment_count > 0
        or any(term in text for term in document_terms)
        or parsed.get("Document Type")
    )

def _ops_has_exception_signal(record, subject: str = "", body: str = "") -> bool:
    """
    Only return True for real exception/escalation language.

    Do NOT treat priority='Critical' by itself as an operations case.
    A dispatcher-critical item can still be a normal booking update.
    """
    text = _ops_text_blob(
        subject,
        body,
        record.get("action_required", "") if record is not None else "",
        record.get("case_next_action", "") if record is not None else "",
    )

    exception_terms = [
        "urgent issue",
        "critical issue",
        "escalation",
        "escalated",
        "customer complaint",
        "complaint",
        "dispute",
        "problem",
        "failed",
        "failure",
        "rejected",
        "cannot pickup",
        "can't pickup",
        "cannot pick up",
        "driver issue",
        "customs hold",
        "terminal hold",
        "line hold",
        "freight hold",
        "hold at port",
        "demurrage",
        "per diem",
        "storage charges",
        "missed appointment",
        "missed pickup",
        "missed delivery",
        "no appointment",
        "appointment not available",
        "container not available",
        "not available at terminal",
        "do not release",
        "do not dispatch",
    ]

    return any(term in text for term in exception_terms)
_OPS_STRUCTURED_FIELD_ALIASES = {
    "customer": "Customer",
    "client": "Customer",
    "booking": "Booking Number",
    "booking number": "Booking Number",
    "bkg": "Booking Number",
    "bkg #": "Booking Number",
    "container": "Container Number",
    "container number": "Container Number",
    "cntr": "Container Number",
    "size": "Size",
    "service flow": "TYPE",
    "type": "TYPE",
    "origin": "Port",
    "pickup": "Port",
    "pickup location": "Port",
    "port": "Port",
    "destination": "Warehouse",
    "delivery": "Warehouse",
    "delivery location": "Warehouse",
    "warehouse": "Warehouse",
    "address": "Address",
    "delivery date": "Delivery Need Date",
    "delivery need date": "Delivery Need Date",
    "lfd": "LFD",
    "document cutoff": "Document Cutoff",
    "cutoff": "Document Cutoff",
    "reference": "Reference Number",
    "reference number": "Reference Number",
    "ref": "Reference Number",
}


def _ops_parse_structured_email_fields(subject: str, body: str) -> dict:
    """
    Parse simple dispatcher/customer emails with key/value fields.

    Handles formats like:
    Customer: Victor Marine Booking: BK070826 Container: TGHU0708

    Also handles multiline values:
    Address: 123 South Houston Lane,
    Houston TX 77450
    """
    raw_text = f"{ops._safe_str(subject)}\n{ops._safe_str(body)}"
    if not raw_text.strip():
        return {}

    labels = sorted(_OPS_STRUCTURED_FIELD_ALIASES.keys(), key=len, reverse=True)
    label_pattern = "|".join(re.escape(label) for label in labels)

    # Put every known label on its own line so inline fields can be parsed.
    normalized = re.sub(
        rf"(?i)\b({label_pattern})\s*:",
        lambda match: f"\n{match.group(1)}:",
        raw_text,
    )

    matches = list(
        re.finditer(
            rf"(?im)^\s*({label_pattern})\s*:\s*",
            normalized,
        )
    )

    fields = {}

    for index, match in enumerate(matches):
        raw_label = match.group(1).strip().lower()
        canonical_label = _OPS_STRUCTURED_FIELD_ALIASES.get(raw_label)
        if not canonical_label:
            continue

        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = normalized[value_start:value_end].strip()
        value = re.sub(r"\s+", " ", value).strip(" -:\n\t")

        if value:
            fields[canonical_label] = value

    # Regex fallbacks for common references.
    tokens = ops._extract_reference_tokens(raw_text)

    if not ops._safe_str(fields.get("Booking Number", "")):
        booking = tokens.get("booking_number")
        if booking:
            fields["Booking Number"] = booking

    if not ops._safe_str(fields.get("Container Number", "")):
        container = tokens.get("container_number")
        if container:
            fields["Container Number"] = container

    if not ops._safe_str(fields.get("Reference Number", "")):
        reference = tokens.get("reference_number")
        if reference:
            fields["Reference Number"] = reference

    # Normalize common service flow values.
    service_flow = ops._safe_str(fields.get("TYPE", ""))
    if service_flow:
        lowered_flow = service_flow.lower()
        if "local export" in lowered_flow:
            fields["TYPE"] = "Local Export"
        elif "local import" in lowered_flow:
            fields["TYPE"] = "Local Import"
        elif "export" in lowered_flow:
            fields["TYPE"] = "Export"
        elif "import" in lowered_flow:
            fields["TYPE"] = "Import"

    return fields

def _ops_parse_fcl_booking_confirmation_fields(subject: str, body: str) -> dict:
    """
    Parse FCL booking confirmations like Continental / Fleur De Lis.

    Handles patterns such as:
    NUMBER OF CNTRS CARGO DESCRIPTION
    4 X 40HC RESIN NON HAZ

    Also handles email-body table values:
    RELEASE REF, CONTAINER QTY, WAREHOUSE, BOOKING, DOC CUT OFF, PORT CUTOFF.
    """

    text = f"{ops._safe_str(subject)}\n{ops._safe_str(body)}"
    clean = re.sub(r"[ \t]+", " ", text)
    clean = re.sub(r"\r\n?", "\n", clean)
    upper = clean.upper()

    fields = {}

    def first_match(pattern: str, flags=re.I | re.S) -> str:
        match = re.search(pattern, clean, flags)
        return ops._safe_str(match.group(1)).strip() if match else ""

    def clean_value(value: str) -> str:
        value = ops._safe_str(value).strip()
        value = re.sub(r"\s+", " ", value)
        return value.strip(" :|-")

    # Booking / references
    booking = (
        first_match(r"\bBOOKING\s*(?:NUMBER|NO\.?|#)?\s*:?\s*([A-Z0-9-]{6,})", re.I)
        or first_match(r"\bHOUSE BILL OF LADING\s+([A-Z0-9-]{6,})", re.I)
        or first_match(r"\bOCEAN BILL OF LADING\s+([A-Z0-9-]{6,})", re.I)
    )

    if booking:
        fields["Booking Number"] = clean_value(booking)

    reference = (
        first_match(r"\bRELEASE REF\s*:?\s*([A-Z0-9/.-]+)", re.I)
        or first_match(r"\bREFERENCE NUMBER\s*:?\s*([A-Z0-9/.-]+)", re.I)
        or first_match(r"\bORDER NUMBERS\s+([A-Z0-9/.-]+)", re.I)
    )

    if reference:
        fields["Reference Number"] = clean_value(reference)

    # Customer
    if "CONTINENTAL INDUSTRIES GROUP" in upper:
        fields["Customer"] = "Continental Industries Group"

    # Service flow
    export_terms = [
        "FULL RETURN",
        "PORT OF LOADING",
        "PORT OF DISCHARGE",
        "EXPORTING CARRIER",
        "CARGO CUT OFF",
        "VGM CUT OFF",
    ]

    if any(term in upper for term in export_terms):
        fields["TYPE"] = "Export"

    # Container quantity and size
    qty_size = None

    qty_size_patterns = [
        r"\bNUMBER OF CNTRS\s+CARGO DESCRIPTION\s+(\d+)\s*[Xx]\s*([0-9]{2}\s*[A-Z]{0,3})\s+([A-Z0-9 /.-]+)",
        r"\bCONTAINER QTY\s*:?\s*(\d+)\s*[Xx]\s*([0-9]{2}\s*[A-Z]{0,3})",
        r"\bSEA FREIGHT\s*-\s*(\d+)\s+([0-9]{2}\s*[A-Z]{0,3})\b",
        r"\b(\d+)\s*[Xx]\s*(40HC|40HQ|40|20|45HC)\b",
    ]

    for pattern in qty_size_patterns:
        match = re.search(pattern, clean, re.I | re.S)
        if match:
            qty_size = match
            break

    if qty_size:
        try:
            fields["Container Qty"] = str(int(qty_size.group(1)))
        except Exception:
            fields["Container Qty"] = qty_size.group(1)

        fields["Size"] = clean_value(qty_size.group(2)).upper().replace(" ", "")

        if len(qty_size.groups()) >= 3:
            commodity = clean_value(qty_size.group(3))
            commodity = re.split(
                r"\bSUPPLIER\b|\bWAREHOUSE\b|\bPICK-UP\b|\bTHE CHARGES\b",
                commodity,
                flags=re.I,
            )[0].strip()
            if commodity:
                fields["Commodity"] = commodity.title()

    # Locations
    empty_pickup = first_match(r"\bEMPTY PICK UP\s+FULL RETURN\s+(.+?)\s+Bayport Terminal", re.I | re.S)
    if empty_pickup:
        fields["Empty Pickup"] = clean_value(empty_pickup)

    if "BAYPORT TERMINAL" in upper:
        fields["Full Return Terminal"] = "Bayport Terminal"
        fields["Port"] = "Bayport Terminal"

    warehouse = first_match(r"\bWAREHOUSE\s*:?\s*([A-Z0-9 &.,/-]+)", re.I)
    if warehouse:
        fields["Warehouse"] = clean_value(warehouse).title()

    # Carrier / vessel
    vessel = first_match(r"\bVESSEL\s*:?\s*([A-Z0-9 .-]+)", re.I)
    if vessel:
        fields["Vessel Name"] = clean_value(vessel).upper()

    if "OCEAN NETWORK EXPRESS" in upper or re.search(r"\bONE\b", upper):
        fields["Steamship Line"] = "ONE"

    carrier = first_match(r"\bEXPORTING CARRIER\s+PLACE OF RECEIPT\s+(.+?)\s+Houston", re.I | re.S)
    if carrier:
        fields["Exporting Carrier"] = clean_value(carrier)

    # Ports
    if re.search(r"\bPORT OF LOADING\s+PORT OF DISCHARGE\s+Houston\s+Kaohsiung", clean, re.I):
        fields["Port Of Loading"] = "Houston"
        fields["Port Of Discharge"] = "Kaohsiung"
        fields["Place Of Delivery"] = "Kaohsiung"

    # Cutoffs / dates
    date_patterns = {
        "Opens For Receiving": r"\bOPENS FOR RECEIVING\s*:?\s*([0-9A-Za-z:-]+\s+[0-9:]+)",
        "Document Cutoff": r"\bDOCUMENTATION CUT OFF\s*:?\s*([0-9A-Za-z:-]+\s+[0-9:]+)",
        "VGM Cutoff": r"\bVGM CUT OFF\s*:?\s*([0-9A-Za-z:-]+\s+[0-9:]+)",
        "Cargo Cutoff": r"\bCARGO CUT OFF\s*:?\s*([0-9A-Za-z:-]+\s+[0-9:]+)",
        "Sailing Date": r"\bSAILING\s*:?\s*([0-9A-Za-z:-]+)",
        "ETA Date": r"\bETA\s*:?\s*([0-9A-Za-z:-]+)",
        "Port Cutoff": r"\bPORT CUTOFF\s*:?\s*([0-9/.-]+)",
    }

    for field_name, pattern in date_patterns.items():
        value = first_match(pattern, re.I)
        if value:
            fields[field_name] = clean_value(value)

    # Rate sheet fields
    rate_per_container = first_match(r"\b([0-9]+\.[0-9]{2})/CN\b", re.I)
    if rate_per_container:
        fields["Rate Per Container"] = rate_per_container

    job_total = first_match(r"\bJob Total\s+([0-9,]+\.[0-9]{2})", re.I)
    if job_total:
        fields["Rate Total"] = job_total.replace(",", "")

    fields["_multi_container_booking"] = bool(
        int(fields.get("Container Qty") or 0) > 1
        if str(fields.get("Container Qty", "")).isdigit()
        else False
    )

    return {
        key: value
        for key, value in fields.items()
        if ops._safe_str(value).strip() != ""
    }    
_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _ops_parse_loose_date(value: str) -> str:
    value = ops._safe_str(value).strip().lower().rstrip(".")

    if not value:
        return ""

    # YYYY-MM-DD
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", value)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # MM/DD/YYYY or MM/DD
    match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d{2}))?\b", value)
    if match:
        month, day, year = match.groups()
        year = int(year or datetime.now().year)
        return f"{year:04d}-{int(month):02d}-{int(day):02d}"

    # July 14 or Jul 14, 2026
    match = re.search(
        r"\b("
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?"
        r")\s+(\d{1,2})(?:,?\s*(20\d{2}))?\b",
        value,
        flags=re.I,
    )
    if match:
        month_name, day, year = match.groups()
        month = _MONTH_NAME_TO_NUMBER.get(month_name.lower())
        year = int(year or datetime.now().year)
        if month:
            return f"{year:04d}-{month:02d}-{int(day):02d}"

    return ""
def _ops_numeric_or_none(value) -> str | None:
    """Coerce a rate-like value to a clean numeric string, or None.

    order_intake_drafts.rate_per_container/rate_total are `numeric` columns —
    inserting '' (the default when a value is missing) raises
    "invalid input syntax for type numeric", so callers must pass NULL
    instead of an empty string.
    """
    text = ops._safe_str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.]", "", text)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        float(cleaned)
    except ValueError:
        return None
    return cleaned


def _ops_clean_pending_draft_value(value: str) -> str:
    value = ops._safe_str(value).strip()

    if not value:
        return ""

    stop_phrases = [
        "please confirm",
        "thank you",
        "thanks,",
        "regards,",
        "sent from",
    ]

    lowered = value.lower()
    cut_index = None

    for phrase in stop_phrases:
        found_index = lowered.find(phrase)
        if found_index >= 0:
            cut_index = found_index if cut_index is None else min(cut_index, found_index)

    if cut_index is not None:
        value = value[:cut_index].strip(" ,.;:-")

    return value.strip()


def _ops_pending_draft_fields_from_parsed(parsed: dict) -> dict:
    """
    Map parsed email fields into order_intake_drafts columns.

    This keeps the pending draft current while client/dispatcher
    go back and forth before a load is created.
    """

    parsed = parsed if isinstance(parsed, dict) else {}

    def pick(*keys: str) -> str:
        for key in keys:
            value = ops._safe_str(parsed.get(key, "")).strip()
            if value:
                return _ops_clean_pending_draft_value(value)
        return ""

    fields = {
        "customer": pick("Customer", "customer", "Contact Company"),
        "booking_number": pick("Booking Number", "booking_number", "Booking"),
        "container_number": pick("Container Number", "container_number", "Container"),
        "service_flow": pick("TYPE", "Type", "Service Flow", "service_flow"),
        "origin_port": pick("Port", "Origin", "Origin Port", "Pickup", "Pickup Terminal"),
        "destination_warehouse": pick("Warehouse", "Destination", "Destination Warehouse"),
        "delivery_address": pick("Address", "Delivery Address"),
        "delivery_need_date": pick("Delivery Need Date", "delivery_need_date", "Need Date"),
        "lfd": pick("LFD", "Last Free Day"),
        "container_size": pick("Size", "Container Size"),
        "reference_number": pick("Reference Number", "reference_number", "Reference"),
        "container_qty": pick("Container Qty", "container_qty", "Container Quantity", "Number Of Containers"),
        "commodity": pick("Commodity", "commodity", "Cargo Description"),
        "empty_pickup_location": pick("Empty Pickup", "empty_pickup_location"),
        "full_return_terminal": pick("Full Return Terminal", "full_return_terminal"),
        "port_of_loading": pick("Port Of Loading", "port_of_loading"),
        "port_of_discharge": pick("Port Of Discharge", "port_of_discharge"),
        "opens_for_receiving": pick("Opens For Receiving", "opens_for_receiving"),
        "vgm_cutoff": pick("VGM Cutoff", "vgm_cutoff"),
        "cargo_cutoff": pick("Cargo Cutoff", "cargo_cutoff"),
        "sailing_date": pick("Sailing Date", "sailing_date"),
        "eta_date": pick("ETA Date", "eta_date"),
        "rate_per_container": pick("Rate Per Container", "rate_per_container"),
        "rate_total": pick("Rate Total", "rate_total"),
    }

    # Clean common service flow values.
    service_flow = fields.get("service_flow", "")
    if service_flow:
        fields["service_flow"] = normalize_service_flow(service_flow, default=service_flow)
    qty_text = ops._safe_str(fields.get("container_qty", "")).strip()
    qty_match = re.search(r"\d+", qty_text)

    if qty_match:
        fields["container_qty"] = qty_match.group(0)
    return {
        key: value
        for key, value in fields.items()
        if ops._safe_str(value).strip()
    }

def _ops_pending_order_reply_updates(subject: str, body: str) -> dict:
    """
    Extract small update instructions from pending-order reply emails.

    Example:
    'I changed my mind again delivery need date is july 14.'
    """

    text = f"{ops._safe_str(subject)}\n{ops._safe_str(body)}"
    lowered = text.lower()

    updated_fields = {}
    summary_parts = []

    date_patterns = [
        r"(?:delivery\s+need\s+date|need\s+date|delivery\s+date|deliver\s+on|delivery\s+on)\s*(?:is|to|:)?\s*([a-zA-Z]+\s+\d{1,2}(?:,?\s*20\d{2})?|\d{1,2}/\d{1,2}(?:/20\d{2})?|20\d{2}-\d{1,2}-\d{1,2})",
        r"(?:change|changed|update|updated|move|moved)\s+(?:the\s+)?(?:delivery\s+need\s+date|need\s+date|delivery\s+date)\s*(?:to|for|as)?\s*([a-zA-Z]+\s+\d{1,2}(?:,?\s*20\d{2})?|\d{1,2}/\d{1,2}(?:/20\d{2})?|20\d{2}-\d{1,2}-\d{1,2})",
    ]

    for pattern in date_patterns:
        match = re.search(pattern, lowered, flags=re.I)
        if match:
            parsed_date = _ops_parse_loose_date(match.group(1))
            if parsed_date:
                updated_fields["Delivery Need Date"] = parsed_date
                summary_parts.append(f"Updated delivery need date to {parsed_date}.")
                break

    address_match = re.search(
        r"(?:address|delivery address)\s*(?:is|to|:)\s*([^\n\r]+)",
        text,
        flags=re.I,
    )
    if address_match:
        address = address_match.group(1).strip()
        if address:
            updated_fields["Address"] = address
            summary_parts.append(f"Updated delivery address to {address}.")

    return {
        "updated_fields": updated_fields,
        "summary": " ".join(summary_parts).strip(),
    }


def _ops_pending_order_reply_body(
    *,
    booking: str,
    update_summary: str,
) -> str:
    booking = ops._safe_str(booking).strip()
    update_summary = ops._safe_str(update_summary).strip()

    booking_text = f" for booking {booking}" if booking else ""

    if update_summary:
        return (
            "Hello,\n\n"
            f"Thank you for the update. {update_summary} "
            f"Our dispatch team will continue reviewing{booking_text} and will follow up if anything else is needed.\n\n"
            "Thank you,\n"
            "CaliTrans Dispatch"
        )

    return (
        "Hello,\n\n"
        f"Thank you for the update{booking_text}. "
        "Our dispatch team will continue reviewing and will follow up if anything else is needed.\n\n"
        "Thank you,\n"
        "CaliTrans Dispatch"
    )

_CUSTOMER_SERVICE_PHRASE_DENYLIST = {
    "otr import",
    "otr export",
    "local import",
    "local export",
    "import",
    "export",
    "drayage",
    "otr",
}


def _resolve_confirmed_container_qty(*, existing_qty, edited_qty, confirmed: bool) -> int | None:
    """The Containers Required number_input always needs *some* numeric
    value to display even when nothing is known yet, so it falls back to
    showing 1 - but that displayed placeholder must never be silently
    written back as a real confirmed quantity. Only persist edited_qty when
    the dispatcher has explicitly checked the confirmation box this save;
    otherwise the draft stays Needs Review regardless of what the widget
    happens to be showing."""
    if confirmed:
        return int(edited_qty)
    return None


def _ops_is_customer_service_phrase(value: str) -> bool:
    """True when a candidate Customer value is nothing but a move/service
    description (e.g. "OTR IMPORT" pulled off the trailing end of a
    subject line) rather than an actual customer or company name. Never
    infer a customer from these phrases - only reject an exact match, so a
    real company name that happens to contain "import" (e.g. "Import
    Trading Co") is still accepted."""
    return value.strip().lower() in _CUSTOMER_SERVICE_PHRASE_DENYLIST


def _ops_merge_parsed_fields(base: dict, source: dict, *, force: bool = False) -> dict:
    """
    Merge parsed fields safely.

    force=False: fill blanks only.
    force=True: explicit key/value email fields can replace weak or bad parser output.
    """
    merged = dict(base or {})
    source = source if isinstance(source, dict) else {}

    candidate_fields = []
    normalized_source = {}
    for key, value in source.items():
        if str(key).startswith("_"):
            continue

        clean_value = ops._safe_str(value).strip()
        if not clean_value:
            continue

        canonical_key = "Customer" if key == "customer" else key
        if canonical_key == "Customer" and _ops_is_customer_service_phrase(clean_value):
            continue

        incoming_valid = validate_field_value(
            canonical_key,
            clean_value,
            source="structured_email" if force else "hybrid_email",
            method="structured_field" if force else "hybrid_field",
        )[0]
        if not incoming_valid:
            continue
        candidate_fields.append(canonical_key)
        normalized_source[canonical_key] = clean_value

    if force and candidate_fields:
        merged, _, _ = reconcile_parsed_sources(
            merged,
            normalized_source,
            fields=list(dict.fromkeys(candidate_fields)),
        )
        return merged

    for key in candidate_fields:
        clean_value = normalized_source[key]
        existing_value = merged.get(key, "")
        existing_valid = validate_field_value(
            key,
            existing_value,
            source="persisted",
            method="parsed_value",
        )[0]
        if not existing_value or not existing_valid:
            merged[key] = clean_value

    return merged

def _ops_order_evidence(parsed, tokens, subject: str, body: str, record=None) -> dict:
    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}

    booking = (
        ops._safe_str(parsed.get("Booking Number", ""))
        or ops._safe_str(tokens.get("booking_number", ""))
        or ops._safe_str(record.get("booking_hint", "") if record is not None else "")
    )

    container = (
        ops._safe_str(parsed.get("Container Number", ""))
        or ops._safe_str(tokens.get("container_number", ""))
        or ops._safe_str(record.get("container_hint", "") if record is not None else "")
    )

    reference = (
        ops._safe_str(parsed.get("Reference Number", ""))
        or ops._safe_str(tokens.get("reference_number", ""))
        or ops._safe_str(record.get("reference_hint", "") if record is not None else "")
    )

    customer = (
        ops._safe_str(parsed.get("Customer", ""))
        or ops._safe_str(record.get("customer_hint", "") if record is not None else "")
        or ops._safe_str(record.get("client_name", "") if record is not None else "")
    )

    pickup = (
        ops._safe_str(parsed.get("Pickup", ""))
        or ops._safe_str(parsed.get("Pickup Location", ""))
        or ops._safe_str(parsed.get("Port", ""))
        or ops._safe_str(record.get("port_hint", "") if record is not None else "")
    )

    delivery = (
        ops._safe_str(parsed.get("Delivery", ""))
        or ops._safe_str(parsed.get("Delivery Location", ""))
        or ops._safe_str(parsed.get("Address", ""))
        or ops._safe_str(parsed.get("Warehouse", ""))
        or ops._safe_str(record.get("delivery_hint", "") if record is not None else "")
        or ops._safe_str(record.get("warehouse_hint", "") if record is not None else "")
    )

    new_order_signal = _ops_has_true_new_order_signal(subject, body, record)
    update_signal = _ops_has_reply_or_update_signal(subject, body, record)

    return {
        "customer": customer,
        "booking": booking,
        "container": container,
        "reference": reference,
        "pickup": pickup,
        "delivery": delivery,
        "has_customer": bool(customer),
        "has_identifier": bool(booking or container or reference),
        "has_lane": bool(pickup and delivery),
        "new_order_signal": new_order_signal,
        "update_signal": update_signal,
        "create_order_ready": bool(
            new_order_signal
            and not update_signal
            and customer
            and (booking or container or reference)
            and pickup
            and delivery
        ),
    }


def _ops_final_dispatcher_decision(
    *,
    record,
    parsed,
    tokens,
    detected_type: str,
    subject: str,
    body: str,
    matched_load_id,
    confidence: int,
) -> dict:
    evidence = _ops_order_evidence(parsed, tokens, subject, body, record)

    request_type = ops._safe_str(record.get("request_type_clean") or record.get("request_type") or detected_type)
    current_queue = ops._safe_str(record.get("dispatcher_queue") or record.get("work_queue"))
    text = _ops_text_blob(
        request_type,
        current_queue,
        detected_type,
        record.get("work_item", ""),
        record.get("recommended_action", ""),
        subject,
        body,
    )

    has_document = _ops_has_document_signal(record, parsed, subject, body)
    has_exception = _ops_has_exception_signal(record, subject, body)

    booking_confirmation = ops._is_booking_confirmation(
        subject,
        body,
        parsed,
    )

    actual_billing_request = ops._has_actual_billing_request(
        subject,
        body,
    )
    decision = {
        "work_type": request_type or detected_type or "Work Item",
        "queue": current_queue or "Needs Review",
        "recommended_action": "Review / Reply",
        "llm_required": False,
        "llm_reason": "",
        "allow_create_order": False,
        "allow_update_load": False,
        "allow_attach_document": False,
        "allow_open_case": False,
        "allow_close": True,
        "action_buttons": ["Reply", "Close / No Action"],
        "reason": "",
        "evidence": evidence,
    }

    # Pending new-order replies should stay in New Orders until a real load exists.
    is_pending_order_reply = _ops_is_pending_order_reply(
        subject=subject,
        body=body,
        record=record,
        matched_load_id=matched_load_id,
    )

    if is_pending_order_reply:
        missing_fields = _ops_pending_order_missing_fields(parsed, tokens, evidence)
        draft_ready = not bool(missing_fields)

        decision["work_type"] = "Pending Order Reply"
        decision["queue"] = "New Orders"
        decision["recommended_action"] = "Review Updated Order Draft"
        decision["allow_create_order"] = draft_ready
        decision["allow_update_load"] = False
        decision["allow_attach_document"] = bool(has_document)
        decision["allow_open_case"] = bool(has_exception)
        decision["llm_required"] = False
        decision["llm_reason"] = ""
        decision["reason"] = (
            "This is a reply in a pending new-order thread. "
            "Keep updating the draft until the dispatcher creates the order."
        )
        decision["action_buttons"] = ["Review Order Draft"]

        if has_exception:
            decision["action_buttons"].append("Open Case")

        decision["action_buttons"].append("Close / No Action")
        return decision
        # Authoritative booking confirmation must win before Documents or Billing.
    if booking_confirmation and not actual_billing_request:
        missing_fields = _ops_pending_order_missing_fields(
            parsed,
            tokens,
            evidence,
        )

        draft_ready = not bool(missing_fields)

        decision["work_type"] = "New Booking"
        decision["queue"] = "New Orders"
        decision["recommended_action"] = (
            "Review Order Draft"
            if draft_ready
            else "Review Extracted Booking Details"
        )
        decision["allow_create_order"] = draft_ready
        decision["allow_update_load"] = False
        decision["allow_attach_document"] = bool(has_document)
        decision["allow_open_case"] = bool(has_exception)
        decision["llm_required"] = False
        decision["llm_reason"] = ""
        decision["reason"] = (
            "A booking confirmation was detected. "
            "Passive invoice, rate-sheet, and bill-of-lading language "
            "does not make this a Billing request."
        )

        decision["action_buttons"] = ["Review Order Draft"]

        if has_document:
            decision["action_buttons"].append("Review Attachment")

        if has_exception:
            decision["action_buttons"].append("Open Case")

        decision["action_buttons"].append("Close / No Action")

        return decision
    # Existing load/update path should win only after a real load exists,
    # or when the message is clearly not part of a pending order.
    is_new_order = bool(evidence.get("new_order_signal") and not evidence.get("update_signal"))

    if evidence.get("update_signal") or request_type == "Booking Update" or (
        "existing" in current_queue.lower() and not is_new_order
        ):
        decision["work_type"] = "Booking Update"
        decision["queue"] = "Existing Load Updates"
        decision["recommended_action"] = "Update Existing Load" if matched_load_id else "Find / Match Existing Load"
        decision["allow_update_load"] = bool(matched_load_id)
        decision["allow_attach_document"] = bool(has_document)
        decision["allow_open_case"] = bool(has_exception)
        decision["llm_required"] = bool(not matched_load_id and confidence < 85)
        decision["llm_reason"] = "Run AI only if load matching is unclear."
        decision["reason"] = "This looks like a reply/update to an existing booking, not a new order."

        buttons = []
        if matched_load_id:
            buttons.append("Update Load")
        else:
            buttons.append("Find Load Match")

        buttons.append("Reply")

        if has_document:
            buttons.append("Attach Document")

        # Only show Open Case for true exceptions/escalations,
        # not normal booking updates or critical-priority work.
        if has_exception:
            buttons.append("Open Case")

        buttons.append("Close / No Action")
        decision["action_buttons"] = buttons
        return decision

    if "quote" in text:
        decision["work_type"] = "Quote Request"
        decision["queue"] = "Quotes"
        decision["recommended_action"] = "Create Quote / Draft Reply"
        decision["llm_required"] = True
        decision["llm_reason"] = "Quote requests should be reviewed before sending pricing."
        decision["action_buttons"] = ["Create Quote", "Draft Reply", "Run Deep AI Review", "Archive"]
        decision["reason"] = "This appears to be a quote request."
        return decision

    if (
        has_document
        and not booking_confirmation
        and (
        "document" in text
        or "pod" in text
        or "bol" in text
        or "invoice" in text
        )
        ):
        decision["work_type"] = "Document"
        decision["queue"] = "Documents"
        decision["recommended_action"] = "Attach Document"
        decision["allow_attach_document"] = True
        decision["llm_required"] = False
        decision["action_buttons"] = ["Attach Document", "Reply", "Send to Billing", "Archive"]
        decision["reason"] = "Document or attachment signal found."
        return decision

    if actual_billing_request:
        decision["work_type"] = "Billing"
        decision["queue"] = "Billing"
        decision["recommended_action"] = "Send to Billing"
        decision["llm_required"] = False
        decision["action_buttons"] = ["Send to Billing", "Reply", "Archive"]
        decision["reason"] = "Billing signal found."
        return decision

    if evidence["create_order_ready"]:
        decision["work_type"] = "New Booking"
        decision["queue"] = "New Orders"
        decision["recommended_action"] = "Review Order Draft"
        decision["allow_create_order"] = True

        # Dispatcher review is required, but deep AI is not required
        # when structured fields are already extracted with high confidence.
        decision["llm_required"] = bool(confidence < 85)
        decision["llm_reason"] = (
            "Run Deep AI Review only if the extracted order details look wrong or incomplete."
            if confidence < 85
            else ""
        )

        # Keep the top card informational. Real actions happen in the Order Draft Review panel.
        decision["action_buttons"] = ["Review Order Draft"]
        decision["reason"] = "New order language and required order fields were found."
        return decision
    if evidence["new_order_signal"] and not evidence["create_order_ready"]:
        decision["work_type"] = "Possible New Booking"
        decision["queue"] = "Needs Review"
        decision["recommended_action"] = "Request Missing Order Details"
        decision["llm_required"] = True
        decision["llm_reason"] = "Possible new order, but required order details are missing."
        decision["action_buttons"] = ["Request Missing Info", "Run Deep AI Review", "Reply", "Archive"]
        decision["reason"] = "Possible new order, but not enough data to create a load."
        return decision

    if has_exception:
        decision["work_type"] = request_type or "Exception"
        decision["queue"] = "Needs Review"
        decision["recommended_action"] = "Review Exception"
        decision["allow_open_case"] = True
        decision["llm_required"] = True
        decision["llm_reason"] = "Possible exception or escalation."
        decision["action_buttons"] = ["Reply", "Open Case", "Run Deep AI Review", "Archive"]
        decision["reason"] = "Exception or escalation signal found."
        return decision

    return decision

def _ops_llm_review_required(record, detected_type: str = "") -> bool:
    raw_value = record.get("llm_review_required", None)

    if raw_value is not None and ops._safe_str(raw_value).strip() != "":
        return _ops_is_truthy(raw_value)

    haystack = " ".join(
        [
            ops._safe_str(detected_type),
            ops._safe_str(record.get("request_type", "")),
            ops._safe_str(record.get("work_queue", "")),
            ops._safe_str(record.get("work_item", "")),
            ops._safe_str(record.get("recommended_action", "")),
        ]
    ).lower()

    return any(
        token in haystack
        for token in [
            "quote",
            "new order",
            "booking request",
            "create order",
            "needs review",
            "exception",
            "dispute",
        ]
    )

def _ops_primary_action_label(record, detected_type: str = "") -> str:
    subject = ops._safe_str(record.get("source_subject", ""))
    body = ops._safe_str(record.get("raw_text_preview", "")) or ops._safe_str(record.get("raw_text", ""))
    parsed = ops._coerce_json_dict(record.get("parsed_data"))
    tokens = ops._extract_reference_tokens(f"{subject}\n{body}\n{parsed}")

    decision = _ops_final_dispatcher_decision(
        record=record,
        parsed=parsed,
        tokens=tokens,
        detected_type=detected_type,
        subject=subject,
        body=body,
        matched_load_id=record.get("matched_load_id"),
        confidence=int(record.get("confidence_score") or 0),
    )

    return decision["recommended_action"]
def _ops_action_buttons_for_record(
    record,
    detected_type: str = "",
    *,
    parsed=None,
    tokens=None,
    subject: str = "",
    body: str = "",
    matched_load_id=None,
    confidence: int = 0,
) -> list[str]:
    parsed = parsed if isinstance(parsed, dict) else ops._coerce_json_dict(record.get("parsed_data"))
    tokens = tokens if isinstance(tokens, dict) else ops._extract_reference_tokens(
        f"{subject}\n{body}\n{parsed}"
    )

    decision = _ops_final_dispatcher_decision(
        record=record,
        parsed=parsed,
        tokens=tokens,
        detected_type=detected_type,
        subject=subject or ops._safe_str(record.get("source_subject", "")),
        body=body or ops._safe_str(record.get("raw_text_preview", "")) or ops._safe_str(record.get("raw_text", "")),
        matched_load_id=matched_load_id if matched_load_id is not None else record.get("matched_load_id"),
        confidence=int(confidence or record.get("confidence_score") or 0),
    )

    return decision["action_buttons"]
def _build_dispatcher_decision(
    *,
    selected_id: int,
    record,
    parsed,
    tokens,
    detected_type: str,
    subject: str,
    body: str,
    sender: str,
    matched_load_id,
    confidence: int,
    conversation_key: str,
) -> dict:
    return _ops_final_dispatcher_decision(
        record=record,
        parsed=parsed,
        tokens=tokens,
        detected_type=detected_type,
        subject=subject,
        body=body,
        matched_load_id=matched_load_id,
        confidence=int(confidence or 0),
    )

execute = ops.execute
refresh_data = ops.refresh_data
find_load_match_candidates = ops.find_load_match_candidates
update_intake_classification = ops.update_intake_classification
is_operations_ai_configured = ops.is_operations_ai_configured
create_load_from_intake = ops.create_load_from_intake
create_load_from_inbox_item = ops.create_load_from_inbox_item
update_load_from_inbox_item = ops.update_load_from_inbox_item
create_quote_request_from_intake = ops.create_quote_request_from_intake
intent_agent = ops.intent_agent
operations_parser_agent = ops.operations_parser_agent
load_intelligence_agent = ops.load_intelligence_agent
workflow_agent = ops.workflow_agent
response_agent = ops.response_agent

DISPATCHER_QUEUE_ORDER = [
    "New Orders",
    "Quotes",
    "Existing Load Updates",
    "Appointments / PIN",
    "Documents",
    "Billing",
    "Needs Review",
    "Store Only / Archive",]
def _dispatcher_queue_for_row(row) -> str:
    if row is None or not hasattr(row, "get"):
        return "Needs Review"

    request_type = _safe_str(
        row.get("request_type_clean")
        or row.get("request_type")
    )

    work_queue = _safe_str(row.get("work_queue", ""))
    control_level = _safe_str(row.get("control_level", ""))
    department_lane = _safe_str(row.get("department_lane", ""))
    matched_load_id = _safe_str(row.get("matched_load_id", ""))

    subject = _safe_str(row.get("source_subject", ""))
    preview = (
        _safe_str(row.get("raw_text_preview", ""))
        or _safe_str(row.get("raw_text", ""))
    )

    parsed = ops._coerce_json_dict(row.get("parsed_data"))

    text = " ".join(
        [
            request_type,
            work_queue,
            department_lane,
            subject,
            preview,
        ]
    ).lower()

    is_update = _ops_has_reply_or_update_signal(
        subject,
        preview,
        row,
    )

    is_new_order = _ops_has_true_new_order_signal(
        subject,
        preview,
        row,
    )

    booking_confirmation = ops._is_booking_confirmation(
        subject,
        preview,
        parsed,
    )

    actual_billing_request = ops._has_actual_billing_request(
        subject,
        preview,
    )

    # Terminal/no-action routing remains authoritative.
    if (
        control_level == "Level 3 - No Action / Archive"
        or request_type in {"No Action / FYI", "Spam/Marketing"}
        or work_queue in {
            "Archive",
            "Archive / FYI",
            "Store Only / Archive",
        }
    ):
        return "Store Only / Archive"

    if request_type == "Quote Request" or work_queue in {
        "Quote Request",
        "Quote Requests",
        "Quotes",
    }:
        return "Quotes"

    # A clear booking confirmation wins before Billing and Documents.
    if booking_confirmation and not actual_billing_request:
        return "New Orders"

    if request_type in {
        "New Booking",
        "Pending Order Reply",
        "Pending Order Draft",
        "Pending Order Ready",
    }:
        return "New Orders"

    if is_new_order and not is_update:
        return "New Orders"

    if (
        request_type == "Appointment Update"
        or "appointment" in text
        or " pin " in f" {text} "
    ):
        return "Appointments / PIN"

    # Billing requires an actionable billing request.
    if actual_billing_request:
        return "Billing"

    if (
        request_type == "Billing"
        or work_queue == "Billing"
        or department_lane == "Accounting"
    ):
        # Do not trust stale Billing values when the message is a booking.
        if not booking_confirmation:
            return "Billing"

    if (
        request_type == "POD Request"
        or work_queue == "Documents"
        or ops._operations_document_signal_for_row(row)
    ):
        if not booking_confirmation:
            return "Documents"

    if (
        matched_load_id
        or work_queue in {
            "Existing Loads",
            "Existing Load Updates",
            "Waiting",
        }
        or request_type in {
            "Booking Update",
            "Cancellation",
            "Driver Issue",
            "Port Issue",
        }
        or is_update
    ):
        return "Existing Load Updates"

    if request_type in {
        "Needs Classification",
        "Other",
        "",
        "Customer Request",
        "Missing Information",
    }:
        return "Needs Review"

    return "Needs Review"
def _ops_is_weak_conversation_key(value: str) -> bool:
    text = ops._safe_str(value).strip().lower()

    if not text:
        return True

    weak_prefixes = (
        "email-",
        "intake-",
        "customer-request",
        "cah+",
    )

    if text.startswith(weak_prefixes):
        return True

    # Long email/message-id style values are not good business conversation keys.
    if "@" in text and "." in text:
        return True

    return False


def _ops_business_conversation_key(
    *,
    record,
    parsed,
    tokens,
    fallback_key: str = "",
) -> str:
    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}

    candidates = [
        tokens.get("booking_number"),
        parsed.get("Booking Number"),
        record.get("booking_hint") if record is not None else "",
        tokens.get("container_number"),
        parsed.get("Container Number"),
        record.get("container_hint") if record is not None else "",
        tokens.get("reference_number"),
        parsed.get("Reference Number"),
        record.get("reference_hint") if record is not None else "",
        fallback_key,
    ]

    for candidate in candidates:
        clean = ops._safe_str(candidate).strip()
        if clean and not _ops_is_weak_conversation_key(clean):
            return clean

    return ops._safe_str(fallback_key).strip()

def _ops_active_row_business_key(row) -> str:
    if row is None or not hasattr(row, "get"):
        return ""

    parsed = ops._coerce_json_dict(row.get("parsed_data"))

    subject = ops._safe_str(row.get("source_subject", ""))
    body_preview = (
        ops._safe_str(row.get("raw_text_preview", ""))
        or ops._safe_str(row.get("raw_text", ""))
    )

    tokens = ops._extract_reference_tokens(
        f"{subject}\n{body_preview}\n{json.dumps(parsed, default=str)}"
    )

    candidates = [
        row.get("booking_hint", ""),
        parsed.get("Booking Number", ""),
        tokens.get("booking_number", ""),
        row.get("container_hint", ""),
        parsed.get("Container Number", ""),
        tokens.get("container_number", ""),
        row.get("reference_hint", ""),
        parsed.get("Reference Number", ""),
        tokens.get("reference_number", ""),
        row.get("conversation_key", ""),
    ]

    for candidate in candidates:
        clean = ops._safe_str(candidate).strip()
        if clean and not _ops_is_weak_conversation_key(clean):
            return clean.upper()

    return ""


def _ops_filter_active_conversation_work_items(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hide stale/outbound/replied rows from active work queues.

    Keeps only the latest active row for each booking/container/reference.
    """

    if df is None or df.empty:
        return df

    work_df = df.copy()

    if "id" not in work_df.columns:
        return work_df

    work_df["_ops_business_key"] = work_df.apply(_ops_active_row_business_key, axis=1)

    if "source_received_at" in work_df.columns:
        work_df["_ops_received_sort"] = pd.to_datetime(
            work_df["source_received_at"],
            errors="coerce",
        )
    else:
        work_df["_ops_received_sort"] = pd.NaT

    review_status = (
        work_df.get("review_status_clean", work_df.get("review_status", ""))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    latest_direction = (
        work_df.get("latest_direction", work_df.get("email_direction", "inbound"))
        .fillna("inbound")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    hidden_statuses = {
        "closed",
        "waiting customer",
        "waiting on customer",
        "reply sent",
        "sent",
        "archived",
    }

    hide_row = review_status.isin(hidden_statuses) | latest_direction.eq("outbound")
    work_df = work_df[~hide_row].copy()

    if work_df.empty:
        return work_df.drop(
            columns=["_ops_business_key", "_ops_received_sort"],
            errors="ignore",
        )

    with_key = work_df[work_df["_ops_business_key"].astype(str).str.strip().ne("")].copy()
    without_key = work_df[work_df["_ops_business_key"].astype(str).str.strip().eq("")].copy()

    if not with_key.empty:
        with_key = with_key.sort_values(
            by=["_ops_business_key", "_ops_received_sort", "id"],
            ascending=[True, True, True],
        )
        with_key = with_key.groupby("_ops_business_key", as_index=False).tail(1)

    result_df = pd.concat([with_key, without_key], ignore_index=True)
    result_df = result_df.sort_values(
        by=["_ops_received_sort", "id"],
        ascending=[False, False],
    )

    return result_df.drop(
        columns=["_ops_business_key", "_ops_received_sort"],
        errors="ignore",
    )
def _bulk_archive_obvious_non_dispatch_emails(*, dry_run: bool = True, limit: int = 100) -> dict:
    """
    Classify obvious non-dispatch emails as Spam/Marketing or No Action / FYI.
    """

    patterns = [
        ("save up to", "Spam/Marketing", "Archived as promotional marketing email."),
        ("final hours", "Spam/Marketing", "Archived as promotional marketing email."),
        ("hundreds of carriers", "Spam/Marketing", "Archived as marketing/newsletter email."),
        ("why not you", "Spam/Marketing", "Archived as marketing/newsletter email."),
        ("pay with venmo", "Spam/Marketing", "Archived as promotional marketing email."),
        ("free pizza", "Spam/Marketing", "Archived as promotional marketing email."),
        ("ipo", "Spam/Marketing", "Archived as newsletter/financial promotional email."),
        ("old fort", "Spam/Marketing", "Archived as promotional/newsletter email."),
        ("legal protection", "Spam/Marketing", "Archived as promotional/legal marketing email."),
        ("haul of fame", "Spam/Marketing", "Archived as marketing/newsletter email."),
        ("lucky 13", "Spam/Marketing", "Archived as marketing/newsletter email."),
        ("offer subject to change", "Spam/Marketing", "Archived as promotional marketing email."),
        ("unsubscribe", "Spam/Marketing", "Archived as marketing/newsletter email."),
        ("advertisement", "Spam/Marketing", "Archived as advertising/marketing email."),
        ("happy birthday america", "No Action / FYI", "Archived as holiday/FYI email."),
        ("holiday celebration", "No Action / FYI", "Archived as holiday/FYI email."),
        ("cyber scam email reminder", "No Action / FYI", "Archived as security awareness/FYI email."),
        ("out of office", "No Action / FYI", "Archived as out-of-office auto reply."),
        ("automatic reply", "No Action / FYI", "Archived as automatic reply."),
        ("auto reply", "No Action / FYI", "Archived as automatic reply."),
    ]

    try:
        where_clause = ops._inbox_review_where_clause()
        inbox_df = ops._load_operations_inbox_df(where_clause)
    except Exception as exc:
        return {
            "matched": 0,
            "updated": 0,
            "preview": pd.DataFrame(),
            "error": str(exc),
        }

    if inbox_df.empty:
        return {"matched": 0, "updated": 0, "preview": pd.DataFrame()}

    candidate_df = inbox_df.copy()

    for column in ["source_subject", "source_sender", "raw_text_preview", "raw_text", "request_type", "review_status"]:
        if column not in candidate_df.columns:
            candidate_df[column] = ""

    candidate_df["review_status_clean"] = (
        candidate_df["review_status"].fillna("Open").astype(str).str.strip()
    )

    candidate_df = candidate_df[
        ~candidate_df["review_status_clean"].isin(["Closed", "Archived", "Exported"])
    ].copy()

    if candidate_df.empty:
        return {"matched": 0, "updated": 0, "preview": pd.DataFrame()}

    candidate_df["cleanup_text"] = (
        candidate_df["source_subject"].fillna("").astype(str)
        + " "
        + candidate_df["source_sender"].fillna("").astype(str)
        + " "
        + candidate_df["raw_text_preview"].fillna("").astype(str)
        + " "
        + candidate_df["raw_text"].fillna("").astype(str)
    ).str.lower()

    def _match_cleanup_rule(text: str):
        for pattern, request_type, action_required in patterns:
            if pattern in text:
                return {
                    "matched_pattern": pattern,
                    "new_request_type": request_type,
                    "new_action_required": action_required,
                }
        return None

    matches = []

    for _, row in candidate_df.iterrows():
        rule = _match_cleanup_rule(row.get("cleanup_text", ""))
        if not rule:
            continue

        matches.append(
            {
                "id": int(row.get("id")),
                "source_received_at": row.get("source_received_at", ""),
                "source_sender": row.get("source_sender", ""),
                "source_subject": row.get("source_subject", ""),
                "current_request_type": row.get("request_type", ""),
                "current_review_status": row.get("review_status", ""),
                "matched_pattern": rule["matched_pattern"],
                "new_request_type": rule["new_request_type"],
                "new_action_required": rule["new_action_required"],
            }
        )

        if len(matches) >= int(limit):
            break

    preview_df = pd.DataFrame(matches)

    if dry_run:
        return {"matched": len(preview_df), "updated": 0, "preview": preview_df}

    updated = 0

    for _, row in preview_df.iterrows():
        intake_id = int(row["id"])
        request_type = ops._safe_str(row["new_request_type"])
        action_required = ops._safe_str(row["new_action_required"])

        try:
            ops._execute(
                """
                update order_intake
                set request_type = :request_type,
                    review_status = 'Closed',
                    action_required = :action_required,
                    confidence_score = greatest(coalesce(confidence_score, 0), 95)
                where id = :intake_id
                """,
                {
                    "intake_id": intake_id,
                    "request_type": request_type,
                    "action_required": action_required,
                },
            )
        except Exception:
            continue

        try:
            ops._save_operations_ai_feedback(
                intake_id=intake_id,
                load_id=None,
                source_subject=ops._safe_str(row.get("source_subject", "")),
                source_sender=ops._safe_str(row.get("source_sender", "")),
                ai_suggestion=None,
                final_request_type=request_type,
                final_action_required=action_required,
                correction_type="bulk_rule_archive",
                feedback_notes=f"Bulk cleanup rule applied: {row.get('matched_pattern', '')} -> {action_required}",
            )
        except Exception as exc:
            logger.exception(
                "operations parsed_data persistence failed",
                extra={"intake_id": int(selected_id), "stage": "review_reparse_persistence"},
            )
            failures = list(parsed.get("_parser_failures") or [])
            failures.append(f"review_reparse_persistence: {type(exc).__name__}")
            parsed["_parser_failures"] = failures
            parsed["_needs_review"] = True
            parsed["_confidence"] = min(float(parsed.get("_confidence", 0) or 0), 0.40)
            record["persistence_error"] = "Parsed values could not be saved; dispatcher review is required."

        updated += 1

    refresh_data()

    return {
        "matched": len(preview_df),
        "updated": updated,
        "preview": preview_df,
    } 


def _ops_pending_order_missing_fields(parsed: dict, tokens: dict, evidence: dict) -> list[str]:
    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}

    customer = (
        ops._safe_str(evidence.get("customer"))
        or ops._safe_str(parsed.get("Customer"))
    )

    booking = (
        ops._safe_str(evidence.get("booking"))
        or ops._safe_str(parsed.get("Booking Number"))
        or ops._safe_str(tokens.get("booking_number"))
    )

    container = (
        ops._safe_str(evidence.get("container"))
        or ops._safe_str(parsed.get("Container Number"))
        or ops._safe_str(tokens.get("container_number"))
    )

    service_flow = ops._safe_str(parsed.get("TYPE"))
    origin_port = (
        ops._safe_str(evidence.get("pickup"))
        or ops._safe_str(parsed.get("Port"))
        or ops._safe_str(parsed.get("Pickup"))
        or ops._safe_str(parsed.get("Origin"))
    )

    destination = (
        ops._safe_str(evidence.get("delivery"))
        or ops._safe_str(parsed.get("Warehouse"))
        or ops._safe_str(parsed.get("Address"))
        or ops._safe_str(parsed.get("Destination"))
    )

    missing = []

    if not customer:
        missing.append("Customer")

    if not booking and not container:
        missing.append("Booking or Container")

    if not service_flow:
        missing.append("Service Flow")

    if not origin_port:
        missing.append("Origin / Port")

    if not destination:
        missing.append("Destination / Warehouse / Address")

    return missing


def _ops_is_pending_order_reply(
    *,
    subject: str,
    body: str,
    record,
    matched_load_id,
) -> bool:
    """
    A reply to a new booking thread should stay in New Orders until a load exists.
    Once matched_load_id exists, future replies become Existing Load Updates.
    """

    if matched_load_id:
        return False

    subject_text = ops._safe_str(subject).strip().lower()
    body_text = ops._safe_str(body).strip().lower()
    request_type = ops._safe_str(
        record.get("request_type_clean") or record.get("request_type") if record is not None else ""
    )

    if request_type in {"Pending Order Reply", "Pending Order Draft", "Pending Order Ready"}:
        return True

    if subject_text.startswith(("re:", "fw:", "fwd:")) and "new booking" in subject_text:
        return True

    if subject_text.startswith(("re:", "fw:", "fwd:")) and any(
        token in body_text
        for token in [
            "delivery date",
            "delivery address",
            "warehouse contact",
            "appointment",
            "please update",
            "additional details",
        ]
    ):
        return True

    return False


def _ops_upsert_pending_order_draft(
    *,
    selected_id: int,
    record,
    parsed: dict,
    tokens: dict,
    subject: str,
    sender: str,
    body: str,
    conversation_key: str,
    final_decision: dict,
    direction: str = "inbound",
) -> dict:
    """
    Create/update order_intake_drafts so multi-email new-booking conversations
    stay active until the dispatcher creates the real load/order.
    """

    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}
    final_decision = final_decision if isinstance(final_decision, dict) else {}
    evidence = final_decision.get("evidence", {}) or {}

    clean_conversation_key = ops._safe_str(conversation_key).strip()

    if not clean_conversation_key:
        return {
            "saved": False,
            "error": "Missing conversation key",
            "missing_fields": [],
            "draft_status": "",
            "request_stage": "",
        }

    subject_text = ops._safe_str(subject).strip().lower()

    is_reply = subject_text.startswith(("re:", "fw:", "fwd:"))
    missing_fields = _ops_pending_order_missing_fields(parsed, tokens, evidence)

    if missing_fields:
        draft_status = "Needs Details"
    else:
        draft_status = "Ready To Create"

    request_stage = "Pending Order Reply" if is_reply else "New Booking"

    customer = (
        ops._safe_str(evidence.get("customer"))
        or ops._safe_str(parsed.get("Customer"))
        or ops._safe_str(record.get("customer_hint") if record is not None else "")
        or _ops_sender_name(sender)
    )

    booking_number = (
        ops._safe_str(evidence.get("booking"))
        or ops._safe_str(parsed.get("Booking Number"))
        or ops._safe_str(tokens.get("booking_number"))
    )

    container_number = (
        ops._safe_str(evidence.get("container"))
        or ops._safe_str(parsed.get("Container Number"))
        or ops._safe_str(tokens.get("container_number"))
    )

    service_flow = ops._safe_str(parsed.get("TYPE"))

    origin_port = (
        ops._safe_str(evidence.get("pickup"))
        or ops._safe_str(parsed.get("Port"))
        or ops._safe_str(parsed.get("Pickup"))
        or ops._safe_str(parsed.get("Origin"))
    )

    destination_warehouse = (
        ops._safe_str(parsed.get("Warehouse"))
        or ops._safe_str(parsed.get("Destination"))
    )

    delivery_address = (
        ops._safe_str(parsed.get("Address"))
        or ops._safe_str(evidence.get("delivery"))
    )

    reference_number = (
        ops._safe_str(evidence.get("reference"))
        or ops._safe_str(parsed.get("Reference Number"))
        or ops._safe_str(tokens.get("reference_number"))
    )

    _container_qty_str = ops._safe_str(parsed.get("Container Qty", "")).strip()
    _container_qty_value = int(_container_qty_str) if _container_qty_str.isdigit() else None
    if _container_qty_value is not None and not (1 <= _container_qty_value <= 10):
        # Out-of-range/implausible quantities are treated the same as
        # "unknown" so the dispatcher sees Needs Review instead of a load
        # count that was never actually confirmed.
        _container_qty_value = None

    params = {
        "conversation_key": clean_conversation_key,
        "draft_status": draft_status,
        "request_stage": request_stage,
        "customer": customer,
        "booking_number": booking_number,
        "container_number": container_number,
        "service_flow": service_flow,
        "origin_port": origin_port,
        "destination_warehouse": destination_warehouse,
        "delivery_address": delivery_address,
        "delivery_need_date": ops._safe_str(parsed.get("Delivery Need Date")),
        "lfd": ops._safe_str(parsed.get("LFD")),
        "document_cutoff": ops._safe_str(parsed.get("Document Cutoff")),
        "container_size": ops._safe_str(parsed.get("Size")),
        "pickup_appointment": ops._safe_str(parsed.get("Pickup Appointment")),
        "delivery_appointment": ops._safe_str(parsed.get("Delivery Appointment")),
        "warehouse_contact": ops._safe_str(parsed.get("Contact Name") or parsed.get("Warehouse Contact")),
        "warehouse_phone": ops._safe_str(parsed.get("Contact Phone") or parsed.get("Warehouse Phone")),
        "terminal": ops._safe_str(parsed.get("Terminal")),
        "steamship_line": ops._safe_str(parsed.get("Steamship Line")),
        "vessel_name": ops._safe_str(parsed.get("Vessel Name")),
        "reference_number": reference_number,
        "missing_fields": json.dumps(missing_fields),
        "parsed_data": json.dumps(parsed, default=str),
        "created_from_intake_id": int(selected_id),
        "last_intake_id": int(selected_id),
        "latest_direction": direction,
        "latest_subject": subject,
        "latest_sender": sender,
        "container_qty": _container_qty_value,
        "load_group_key": f"{booking_number or reference_number or clean_conversation_key}",
        "commodity": ops._safe_str(parsed.get("Commodity")),
        "empty_pickup_location": ops._safe_str(parsed.get("Empty Pickup")),
        "full_return_terminal": ops._safe_str(parsed.get("Full Return Terminal")),
        "port_of_loading": ops._safe_str(parsed.get("Port Of Loading")),
        "port_of_discharge": ops._safe_str(parsed.get("Port Of Discharge")),
        "place_of_delivery": ops._safe_str(parsed.get("Place Of Delivery")),
        "exporting_carrier": ops._safe_str(parsed.get("Exporting Carrier")),
        "opens_for_receiving": ops._safe_str(parsed.get("Opens For Receiving")),
        "vgm_cutoff": ops._safe_str(parsed.get("VGM Cutoff")),
        "cargo_cutoff": ops._safe_str(parsed.get("Cargo Cutoff")),
        "sailing_date": ops._safe_str(parsed.get("Sailing Date")),
        "eta_date": ops._safe_str(parsed.get("ETA Date")),
        "rate_per_container": _ops_numeric_or_none(parsed.get("Rate Per Container")),
        "rate_total": _ops_numeric_or_none(parsed.get("Rate Total")),
            }

    try:
        ops._execute(
            """
            insert into public.order_intake_drafts (
                conversation_key,
                draft_status,
                request_stage,
                customer,
                booking_number,
                container_number,
                service_flow,
                origin_port,
                destination_warehouse,
                delivery_address,
                delivery_need_date,
                lfd,
                document_cutoff,
                container_size,
                container_qty,
                load_group_key,
                pickup_appointment,
                delivery_appointment,
                warehouse_contact,
                warehouse_phone,
                terminal,
                steamship_line,
                vessel_name,
                reference_number,
                commodity,
                empty_pickup_location,
                full_return_terminal,
                port_of_loading,
                port_of_discharge,
                place_of_delivery,
                exporting_carrier,
                opens_for_receiving,
                vgm_cutoff,
                cargo_cutoff,
                sailing_date,
                eta_date,
                rate_per_container,
                rate_total,
                missing_fields,
                parsed_data,
                created_from_intake_id,
                last_intake_id,
                latest_direction,
                latest_subject,
                latest_sender,
                last_customer_message_at,
                last_dispatch_message_at
            )
            values (
                :conversation_key,
                :draft_status,
                :request_stage,
                :customer,
                :booking_number,
                :container_number,
                :service_flow,
                :origin_port,
                :destination_warehouse,
                :delivery_address,
                :delivery_need_date,
                :lfd,
                :document_cutoff,
                :container_size,
                :container_qty,
                :load_group_key,
                :pickup_appointment,
                :delivery_appointment,
                :warehouse_contact,
                :warehouse_phone,
                :terminal,
                :steamship_line,
                :vessel_name,
                :reference_number,
                :commodity,
                :empty_pickup_location,
                :full_return_terminal,
                :port_of_loading,
                :port_of_discharge,
                :place_of_delivery,
                :exporting_carrier,
                :opens_for_receiving,
                :vgm_cutoff,
                :cargo_cutoff,
                :sailing_date,
                :eta_date,
                :rate_per_container,
                :rate_total,
                cast(:missing_fields as jsonb),
                cast(:parsed_data as jsonb),
                :created_from_intake_id,
                :last_intake_id,
                :latest_direction,
                :latest_subject,
                :latest_sender,
                case when :latest_direction = 'inbound' then now() else null end,
                case when :latest_direction = 'outbound' then now() else null end
            )
            on conflict (conversation_key)
            do update set
                draft_status = excluded.draft_status,
                request_stage = excluded.request_stage,

                -- Once a dispatcher has confirmed this draft (dispatcher_confirmed_at
                -- is set), automated re-parses from later customer messages stop
                -- overwriting these fields entirely; only an explicit dispatcher
                -- save can change them after that point.
                customer = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.customer else coalesce(nullif(excluded.customer, ''), order_intake_drafts.customer) end,
                booking_number = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.booking_number else coalesce(nullif(excluded.booking_number, ''), order_intake_drafts.booking_number) end,
                container_number = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.container_number else coalesce(nullif(excluded.container_number, ''), order_intake_drafts.container_number) end,
                service_flow = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.service_flow else coalesce(nullif(excluded.service_flow, ''), order_intake_drafts.service_flow) end,
                origin_port = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.origin_port else coalesce(nullif(excluded.origin_port, ''), order_intake_drafts.origin_port) end,
                destination_warehouse = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.destination_warehouse else coalesce(nullif(excluded.destination_warehouse, ''), order_intake_drafts.destination_warehouse) end,
                delivery_address = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.delivery_address else coalesce(nullif(excluded.delivery_address, ''), order_intake_drafts.delivery_address) end,
                delivery_need_date = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.delivery_need_date else coalesce(nullif(excluded.delivery_need_date, ''), order_intake_drafts.delivery_need_date) end,
                lfd = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.lfd else coalesce(nullif(excluded.lfd, ''), order_intake_drafts.lfd) end,
                document_cutoff = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.document_cutoff else coalesce(nullif(excluded.document_cutoff, ''), order_intake_drafts.document_cutoff) end,
                container_size = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.container_size else coalesce(nullif(excluded.container_size, ''), order_intake_drafts.container_size) end,
                container_qty = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.container_qty else coalesce(excluded.container_qty, order_intake_drafts.container_qty) end,
                load_group_key = coalesce(nullif(excluded.load_group_key, ''), order_intake_drafts.load_group_key),
                pickup_appointment = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.pickup_appointment else coalesce(nullif(excluded.pickup_appointment, ''), order_intake_drafts.pickup_appointment) end,
                delivery_appointment = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.delivery_appointment else coalesce(nullif(excluded.delivery_appointment, ''), order_intake_drafts.delivery_appointment) end,
                warehouse_contact = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.warehouse_contact else coalesce(nullif(excluded.warehouse_contact, ''), order_intake_drafts.warehouse_contact) end,
                warehouse_phone = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.warehouse_phone else coalesce(nullif(excluded.warehouse_phone, ''), order_intake_drafts.warehouse_phone) end,
                terminal = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.terminal else coalesce(nullif(excluded.terminal, ''), order_intake_drafts.terminal) end,
                steamship_line = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.steamship_line else coalesce(nullif(excluded.steamship_line, ''), order_intake_drafts.steamship_line) end,
                vessel_name = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.vessel_name else coalesce(nullif(excluded.vessel_name, ''), order_intake_drafts.vessel_name) end,
                reference_number = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.reference_number else coalesce(nullif(excluded.reference_number, ''), order_intake_drafts.reference_number) end,
                commodity = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.commodity else coalesce(nullif(excluded.commodity, ''), order_intake_drafts.commodity) end,
                empty_pickup_location = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.empty_pickup_location else coalesce(nullif(excluded.empty_pickup_location, ''), order_intake_drafts.empty_pickup_location) end,
                full_return_terminal = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.full_return_terminal else coalesce(nullif(excluded.full_return_terminal, ''), order_intake_drafts.full_return_terminal) end,
                port_of_loading = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.port_of_loading else coalesce(nullif(excluded.port_of_loading, ''), order_intake_drafts.port_of_loading) end,
                port_of_discharge = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.port_of_discharge else coalesce(nullif(excluded.port_of_discharge, ''), order_intake_drafts.port_of_discharge) end,
                place_of_delivery = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.place_of_delivery else coalesce(nullif(excluded.place_of_delivery, ''), order_intake_drafts.place_of_delivery) end,
                exporting_carrier = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.exporting_carrier else coalesce(nullif(excluded.exporting_carrier, ''), order_intake_drafts.exporting_carrier) end,
                opens_for_receiving = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.opens_for_receiving else coalesce(nullif(excluded.opens_for_receiving, ''), order_intake_drafts.opens_for_receiving) end,
                vgm_cutoff = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.vgm_cutoff else coalesce(nullif(excluded.vgm_cutoff, ''), order_intake_drafts.vgm_cutoff) end,
                cargo_cutoff = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.cargo_cutoff else coalesce(nullif(excluded.cargo_cutoff, ''), order_intake_drafts.cargo_cutoff) end,
                sailing_date = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.sailing_date else coalesce(nullif(excluded.sailing_date, ''), order_intake_drafts.sailing_date) end,
                eta_date = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.eta_date else coalesce(nullif(excluded.eta_date, ''), order_intake_drafts.eta_date) end,
                rate_per_container = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.rate_per_container else coalesce(excluded.rate_per_container, order_intake_drafts.rate_per_container) end,
                rate_total = case when order_intake_drafts.dispatcher_confirmed_at is not null then order_intake_drafts.rate_total else coalesce(excluded.rate_total, order_intake_drafts.rate_total) end,

                missing_fields = excluded.missing_fields,
                parsed_data = coalesce(order_intake_drafts.parsed_data, '{}'::jsonb) || excluded.parsed_data,

                created_from_intake_id = coalesce(order_intake_drafts.created_from_intake_id, excluded.created_from_intake_id),
                last_intake_id = excluded.last_intake_id,
                latest_direction = excluded.latest_direction,
                latest_subject = excluded.latest_subject,
                latest_sender = excluded.latest_sender,

                last_customer_message_at = case
                    when excluded.latest_direction = 'inbound' then now()
                    else order_intake_drafts.last_customer_message_at
                end,
                last_dispatch_message_at = case
                    when excluded.latest_direction = 'outbound' then now()
                    else order_intake_drafts.last_dispatch_message_at
                end
            """,
            params,
        )

        return {
            "saved": True,
            "error": "",
            "missing_fields": missing_fields,
            "draft_status": draft_status,
            "request_stage": request_stage,
            "conversation_key": clean_conversation_key,
        }

    except Exception as exc:
        return {
            "saved": False,
            "error": str(exc),
            "missing_fields": missing_fields,
            "draft_status": draft_status,
            "request_stage": request_stage,
            "conversation_key": clean_conversation_key,
            }

def _render_new_order_review_panel(
    *,
    selected_id: int,
    record,
    parsed: dict,
    tokens: dict,
    subject: str,
    sender: str,
    body: str,
    conversation_key: str,
    final_decision: dict,
    matched_load_id=None,
    timing=None,
) -> None:
    """
    Review extracted order fields and create a load/order only after dispatcher confirmation.
    """

    if not final_decision.get("show_order_draft_review"):
        return

    draft = dict(final_decision.get("order_draft_projection") or {})

    st.markdown("### Order Draft Review")

    with st.container(border=True):
        st.caption(
            "Review the original email and extracted order fields before creating the order. "
            "These fields are an AI/parser draft until the dispatcher confirms them."
        )

        left_col, right_col = st.columns([1.1, 1.2])

        with left_col:
            st.markdown("#### Original Source Review")
            st.write(f"**From:** {sender}")
            st.write(f"**Sender email:** {_extract_email_address(sender) or '-'}")
            st.write(f"**Subject:** {subject}")
            st.text_area(
                "Original latest customer message",
                value=body,
                height=300,
                disabled=True,
                key=f"new_order_original_email_{selected_id}",
            )
            _render_operations_attachment_preview(
                selected_id=int(selected_id),
                record=record,
                parsed=parsed,
                timing=timing,
            )

        with right_col:
            st.markdown("#### Extracted Order Draft")

            customer = st.text_input(
                "Customer",
                value=ops._safe_str(draft.get("Customer")),
                key=f"new_order_customer_{selected_id}",
            )

            service_flows = ["Import", "Export", "Local Import", "Local Export"]
            projected_flow = normalize_service_flow(
                draft.get("TYPE", ""),
                default="Import",
            )
            service_flow = st.selectbox(
                "Service Flow",
                service_flows,
                index=service_flows.index(projected_flow),
                key=f"new_order_service_flow_{selected_id}",
            )

            booking = st.text_input(
                "Booking Number",
                value=ops._safe_str(draft.get("Booking Number")),
                key=f"new_order_booking_{selected_id}",
            )

            reference = st.text_input(
                "Reference Number",
                value=ops._safe_str(draft.get("Reference Number")),
                key=f"new_order_reference_{selected_id}",
            )

            container = st.text_input(
                "Container Number",
                value=ops._safe_str(draft.get("Container Number")),
                key=f"new_order_container_{selected_id}",
            )

            size = st.text_input(
                "Container Size",
                value=ops._safe_str(draft.get("Size")),
                key=f"new_order_size_{selected_id}",
            )

            port = st.text_input(
                "Origin / Port",
                value=ops._safe_str(draft.get("Port")),
                key=f"new_order_port_{selected_id}",
            )

            terminal = st.text_input(
                "Terminal",
                value=ops._safe_str(draft.get("Terminal")),
                key=f"new_order_terminal_{selected_id}",
            )

            warehouse = st.text_input(
                "Destination / Warehouse",
                value=ops._safe_str(draft.get("Warehouse")),
                key=f"new_order_warehouse_{selected_id}",
            )

            pickup_address = st.text_area(
                "Pickup Address",
                value=ops._safe_str(draft.get("Pickup Address")),
                height=80,
                key=f"new_order_pickup_address_{selected_id}",
            )

            delivery_address = st.text_area(
                "Delivery Address",
                value=ops._safe_str(draft.get("Address")),
                height=80,
                key=f"new_order_delivery_address_{selected_id}",
            )

            delivery_need_date = st.text_input(
                "Delivery Need Date",
                value=ops._safe_str(draft.get("Delivery Need Date")),
                key=f"new_order_delivery_need_date_{selected_id}",
            )

            document_cutoff = st.text_input(
                "Document Cutoff",
                value=ops._safe_str(draft.get("Document Cutoff")),
                key=f"new_order_document_cutoff_{selected_id}",
            )

            lfd = st.text_input(
                "LFD",
                value=ops._safe_str(draft.get("LFD")),
                key=f"new_order_lfd_{selected_id}",
            )

            port_pin = st.text_input(
                "Port PIN",
                value=ops._safe_str(draft.get("Port PIN")),
                key=f"new_order_port_pin_{selected_id}",
            )

            dispatcher_notes = st.text_area(
                "Notes",
                value=ops._safe_str(draft.get("Dispatcher Notes")),
                height=100,
                key=f"new_order_notes_{selected_id}",
            )

            missing_fields = []
            if not customer.strip():
                missing_fields.append("Customer")
            if not booking.strip() and not container.strip():
                missing_fields.append("Booking or Container")
            if not port.strip():
                missing_fields.append("Origin / Port")
            if not warehouse.strip() and not delivery_address.strip():
                missing_fields.append("Destination / Address")

            if missing_fields:
                st.warning("Missing required field(s): " + ", ".join(missing_fields))
            else:
                st.success("Order draft has enough information to create a load.")

            create_disabled = bool(missing_fields)

            if st.button(
                "Create Order From Reviewed Draft",
                key=f"create_order_from_reviewed_draft_{selected_id}",
                width="stretch",
                disabled=create_disabled,
            ):
                creation_notes = dispatcher_notes.strip() or f"Created from Operations Inbox request #{selected_id}"

                result = create_load_from_inbox_item(
                    int(selected_id),
                    {
                        "TYPE": service_flow,
                        "Booking Number": booking.strip(),
                        "Reference Number": reference.strip(),
                        "Customer": customer.strip(),
                        "Container Number": container.strip(),
                        "Port": port.strip(),
                        "Terminal": terminal.strip(),
                        "Warehouse": warehouse.strip(),
                        "Pickup Address": pickup_address.strip(),
                        "Address": delivery_address.strip(),
                        "Document Cutoff": document_cutoff.strip(),
                        "Delivery Need Date": delivery_need_date.strip(),
                        "LFD": lfd.strip(),
                        "Port PIN": port_pin.strip(),
                        "Size": size.strip(),
                        "Status": "New",
                        "Dispatcher Notes": creation_notes,
                    },
                    conversation_key=conversation_key,
                    request_type="New Booking",
                    subject=subject,
                    sender=sender,
                    body=body,
                    case_id=None,
                    case_priority="Normal",
                )

                load_id = result.get("load_id")

                refresh_data()
                st.success(f"Created order/load ID {load_id}.")
                close_work_item(st.session_state)
                st.rerun()


_ROUTE_CARGO_SECTION_TITLES = {
    "Export": "Export Booking Details",
    "Import": "Import Route Details",
    "Local Import": "Local Import Route and Cargo Details",
    "Local Export": "Local Export Route and Cargo Details",
}


def _ops_route_cargo_section_title(service_flow: str) -> str:
    return _ROUTE_CARGO_SECTION_TITLES.get(service_flow, "Route and Cargo Details")


def _render_active_pending_order_draft_panel(
    *,
    selected_id: int,
    selected_conversation_key: str,
    matched_load_id,
    final_decision: dict,
    subject: str,
    sender: str,
) -> None:
    """
    Show and maintain the pending order draft before actual container work
    orders are created.
    """

    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_container_qty(raw) -> int | None:
        """A booking can have up to 10 containers. Anything outside 1-10,
        or unset, is unknown and must surface as Needs Review — never
        silently treated as 1."""
        value = _safe_int(raw, 0)
        return value if 1 <= value <= 10 else None

    show_pending_order_draft = (
        matched_load_id is None
        and bool(selected_conversation_key)
        and (
            final_decision.get("queue") == "New Orders"
            or final_decision.get("work_type") in {
                "New Booking",
                "Possible New Booking",
                "Pending Order Reply",
                "Pending Order Draft",
            }
            or "new booking" in ops._safe_str(subject).lower()
        )
    )

    if not show_pending_order_draft:
        return

    pending_draft_expander = st.expander("Active Pending Order Draft", on_change="rerun")
    if not pending_draft_expander.open:
        return

    try:
        draft_df = ops.read_df(
            """
            select
                conversation_key,
                draft_status,
                request_stage,
                customer,
                booking_number,
                reference_number,
                container_number,
                container_qty,
                containers_created,
                load_group_key,
                service_flow,
                origin_port,
                destination_warehouse,
                delivery_address,
                delivery_need_date,
                lfd,
                container_size,
                commodity,
                empty_pickup_location,
                full_return_terminal,
                port_of_loading,
                port_of_discharge,
                place_of_delivery,
                exporting_carrier,
                vessel_name,
                steamship_line,
                opens_for_receiving,
                document_cutoff,
                vgm_cutoff,
                cargo_cutoff,
                sailing_date,
                eta_date,
                rate_per_container,
                rate_total,
                dispatcher_notes,
                missing_fields,
                created_load_id,
                dispatcher_confirmed_at,
                updated_at
            from public.order_intake_drafts
            where (
                    conversation_key = :conversation_key
                 or booking_number = :conversation_key
                 or container_number = :conversation_key
            )
            order by updated_at desc
            limit 1
            """,
            {"conversation_key": selected_conversation_key},
        )
    except Exception as exc:
        with pending_draft_expander:
            st.warning(f"Could not load pending order draft: {exc}")
        return

    with pending_draft_expander:
        if draft_df is None or draft_df.empty:
            st.info("No pending order draft has been created yet for this conversation.")
            return

        draft = draft_df.iloc[0].to_dict()
        draft_conversation_key = (
            ops._safe_str(draft.get("conversation_key", ""))
            or ops._safe_str(selected_conversation_key)
        )

        container_qty = _normalize_container_qty(draft.get("container_qty"))
        containers_created = max(_safe_int(draft.get("containers_created"), 0), 0)
        remaining_to_create = max((container_qty or 0) - containers_created, 0)
        is_dispatcher_confirmed = bool(draft.get("dispatcher_confirmed_at"))

        if is_dispatcher_confirmed:
            st.caption(
                "Dispatcher-confirmed — later automated re-parses of this "
                "conversation will not overwrite these fields."
            )

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Draft Status", ops._safe_str(draft.get("draft_status", "-")) or "-")
        d2.metric("Stage", ops._safe_str(draft.get("request_stage", "-")) or "-")
        d3.metric("Booking", ops._safe_str(draft.get("booking_number", "-")) or "-")
        d4.metric("Reference", ops._safe_str(draft.get("reference_number", "-")) or "-")

        d5, d6, d7, d8 = st.columns(4)
        d5.metric("Customer", ops._safe_str(draft.get("customer", "-")) or "-")
        d6.metric("Service Flow", ops._safe_str(draft.get("service_flow", "-")) or "-")
        d7.metric("Container Size", ops._safe_str(draft.get("container_size", "-")) or "-")
        d8.metric("Known Container", ops._safe_str(draft.get("container_number", "-")) or "-")

        d9, d10, d11 = st.columns(3)
        d9.metric("Containers Required", container_qty if container_qty is not None else "Needs Review")
        d10.metric("Containers Created", containers_created)
        d11.metric("Remaining", remaining_to_create if container_qty is not None else "-")

        st.write("**Route / Delivery Details**")
        st.write(
            f"Origin: {ops._safe_str(draft.get('origin_port', '-')) or '-'} | "
            f"Destination: {ops._safe_str(draft.get('destination_warehouse', '-')) or '-'} | "
            f"Address: {ops._safe_str(draft.get('delivery_address', '-')) or '-'} | "
            f"Need Date: {ops._safe_str(draft.get('delivery_need_date', '-')) or '-'}"
        )

        draft_service_flow = normalize_service_flow(
            ops._safe_str(draft.get("service_flow", "")),
            default=ops._safe_str(draft.get("service_flow", "")),
        )
        section_title = _ops_route_cargo_section_title(draft_service_flow)
        st.markdown(f"#### {section_title}")

        st.metric("Commodity", ops._safe_str(draft.get("commodity", "-")) or "-")

        if draft_service_flow == "Export":
            b1, b2 = st.columns(2)
            b1.metric("Empty Pickup", ops._safe_str(draft.get("empty_pickup_location", "-")) or "-")
            b2.metric("Full Return", ops._safe_str(draft.get("full_return_terminal", "-")) or "-")

            b4, b5, b6 = st.columns(3)
            b4.metric("Document Cutoff", ops._safe_str(draft.get("document_cutoff", "-")) or "-")
            b5.metric("VGM Cutoff", ops._safe_str(draft.get("vgm_cutoff", "-")) or "-")
            b6.metric("Cargo Cutoff", ops._safe_str(draft.get("cargo_cutoff", "-")) or "-")

            b7, b8, b9 = st.columns(3)
            b7.metric("Sailing", ops._safe_str(draft.get("sailing_date", "-")) or "-")
            b8.metric("ETA", ops._safe_str(draft.get("eta_date", "-")) or "-")
            b9.metric("Carrier", ops._safe_str(draft.get("steamship_line", "-")) or "-")

        missing_fields = draft.get("missing_fields") or []
        if isinstance(missing_fields, str):
            try:
                missing_fields = json.loads(missing_fields)
            except Exception:
                missing_fields = [missing_fields] if missing_fields else []

        if missing_fields:
            st.warning("Missing fields: " + ", ".join(map(str, missing_fields)))
        elif not draft.get("created_load_id"):
            st.success("Draft appears ready for dispatcher review before creating work orders.")

        st.markdown("#### Latest Agreed Order Info")

        service_flow_options = ["", "Import", "Export", "Local Import", "Local Export"]
        current_service_flow = ops._safe_str(draft.get("service_flow", ""))
        service_flow_index = (
            service_flow_options.index(current_service_flow)
            if current_service_flow in service_flow_options
            else 0
        )

        with st.form(
            key=f"pending_order_latest_agreed_form_{selected_id}_{draft_conversation_key}",
            clear_on_submit=False,
        ):
            edit_col1, edit_col2 = st.columns(2)

            with edit_col1:
                edit_customer = st.text_input(
                    "Customer",
                    value=ops._safe_str(draft.get("customer", "")),
                )
                edit_booking = st.text_input(
                    "Booking Number",
                    value=ops._safe_str(draft.get("booking_number", "")),
                )
                edit_reference = st.text_input(
                    "Reference Number",
                    value=ops._safe_str(draft.get("reference_number", "")),
                )
                edit_container = st.text_input(
                    "Known Container Number (optional)",
                    value=ops._safe_str(draft.get("container_number", "")),
                )
                edit_qty = st.number_input(
                    "Containers Required",
                    min_value=1,
                    max_value=10,
                    value=container_qty or 1,
                    step=1,
                    help=(
                        "Needs Review: this defaults to 1 only for display. Check "
                        "the confirmation box below before saving to record a real count."
                        if container_qty is None
                        else None
                    ),
                )
                edit_qty_confirmed = st.checkbox(
                    "Confirm containers required",
                    value=container_qty is not None,
                    help=(
                        "Required before saving persists a container count. Prevents the "
                        "number field's display default from being silently recorded as "
                        "a real confirmed quantity."
                    ),
                )
                edit_size = st.text_input(
                    "Container Size",
                    value=ops._safe_str(draft.get("container_size", "")),
                )

            with edit_col2:
                edit_service_flow = st.selectbox(
                    "Service Flow",
                    service_flow_options,
                    index=service_flow_index,
                )
                edit_origin = st.text_input(
                    "Origin / Port",
                    value=ops._safe_str(draft.get("origin_port", "")),
                )
                edit_destination = st.text_input(
                    "Destination / Warehouse",
                    value=ops._safe_str(draft.get("destination_warehouse", "")),
                )
                edit_need_date = st.text_input(
                    "Delivery Need Date",
                    value=ops._safe_str(draft.get("delivery_need_date", "")),
                    placeholder="YYYY-MM-DD",
                )
                edit_empty_pickup = st.text_input(
                    "Empty Pickup Location",
                    value=ops._safe_str(draft.get("empty_pickup_location", "")),
                )
                edit_full_return = st.text_input(
                    "Full Return Terminal",
                    value=ops._safe_str(draft.get("full_return_terminal", "")),
                )

            edit_address = st.text_area(
                "Delivery Address",
                value=ops._safe_str(draft.get("delivery_address", "")),
                height=80,
            )
            edit_commodity = st.text_input(
                "Commodity",
                value=ops._safe_str(draft.get("commodity", "")),
            )
            edit_notes = st.text_area(
                "Dispatcher Notes / Final Agreement Notes",
                value=ops._safe_str(draft.get("dispatcher_notes", "")),
                height=80,
            )

            save_latest_agreed = st.form_submit_button(
                "Save Latest Agreed Draft Details",
                use_container_width=True,
            )

        if save_latest_agreed:
            saved = ops._update_pending_order_draft_fields(
                conversation_key=draft_conversation_key,
                fields={
                    "customer": edit_customer,
                    "booking_number": edit_booking,
                    "reference_number": edit_reference,
                    "container_number": edit_container,
                    "container_qty": _resolve_confirmed_container_qty(
                        existing_qty=container_qty,
                        edited_qty=edit_qty,
                        confirmed=edit_qty_confirmed,
                    ),
                    "container_size": edit_size,
                    "service_flow": edit_service_flow,
                    "origin_port": edit_origin,
                    "destination_warehouse": edit_destination,
                    "delivery_address": edit_address,
                    "delivery_need_date": edit_need_date,
                    "empty_pickup_location": edit_empty_pickup,
                    "full_return_terminal": edit_full_return,
                    "commodity": edit_commodity,
                    "dispatcher_notes": edit_notes,
                },
                last_intake_id=int(selected_id),
                latest_subject=subject,
                latest_sender=sender,
                request_stage="Dispatcher Confirmed Draft",
                dispatcher_confirmed=True,
            )

            if saved:
                st.success("Latest agreed order draft details were saved.")
                refresh_data()
                st.rerun()
            else:
                st.warning("Draft was not saved. Confirm the pending draft exists and try again.")

        st.markdown("#### Container Work Orders")

        # Recalculate from the values loaded for this render. After saving, the
        # page reruns and reloads the newest values.
        container_qty = _normalize_container_qty(draft.get("container_qty"))
        containers_created = max(_safe_int(draft.get("containers_created"), 0), 0)
        remaining_to_create = max((container_qty or 0) - containers_created, 0)

        if container_qty is None:
            st.warning(
                "Containers Required: Needs Review. Set a valid container count "
                "(1-10) above before creating container work orders."
            )
        elif container_qty > 1:
            st.info(
                f"This booking requires {container_qty} container moves. "
                f"{containers_created} have been created; {remaining_to_create} remain."
            )

        required_for_creation = {
            "Customer": ops._safe_str(draft.get("customer", "")),
            "Booking Number": ops._safe_str(draft.get("booking_number", "")),
            "Service Flow": ops._safe_str(draft.get("service_flow", "")),
            "Container Size": ops._safe_str(draft.get("container_size", "")),
        }
        creation_missing = [
            label for label, value in required_for_creation.items() if not value
        ]

        if creation_missing:
            st.warning(
                "Complete these fields before creating work orders: "
                + ", ".join(creation_missing)
            )

        create_child_loads_disabled = (
            container_qty is None
            or remaining_to_create <= 0
            or bool(creation_missing)
        )

        if st.button(
            f"Create {remaining_to_create} Container Work Order(s)",
            key=f"create_child_container_work_orders_{selected_id}_{draft_conversation_key}",
            use_container_width=True,
            disabled=create_child_loads_disabled,
        ):
            creation_result = create_container_work_orders(
                intake_id=int(selected_id),
                draft=draft,
                conversation_key=draft_conversation_key,
                subject=subject,
                sender=sender,
                container_qty=container_qty,
                containers_created=containers_created,
            )

            created_load_ids = creation_result.get("created_load_ids", [])
            creation_errors = creation_result.get("errors", [])

            if created_load_ids:
                st.success(
                    "Created container work orders: "
                    + ", ".join(map(str, created_load_ids))
                )

            if creation_errors:
                st.warning("Some work orders were not created:\n- " + "\n- ".join(creation_errors))

            refresh_data()
            st.rerun()
def _ops_extract_ai_reply_draft(ai_suggestion: dict) -> str:
    """
    Pull the reply draft text from an AI suggestion response using several possible keys.
    This keeps the UI stable even if the AI response schema changes slightly.
    """

    if not isinstance(ai_suggestion, dict):
        return ""

    direct_keys = [
        "reply_draft",
        "draft_reply",
        "suggested_reply",
        "email_reply",
        "response_draft",
        "customer_reply",
        "reply_body",
    ]

    for key in direct_keys:
        value = ai_suggestion.get(key)
        if ops._safe_str(value).strip():
            return ops._safe_str(value).strip()

    reply_obj = ai_suggestion.get("reply")
    if isinstance(reply_obj, dict):
        for key in ["body", "message", "draft", "text"]:
            value = reply_obj.get(key)
            if ops._safe_str(value).strip():
                return ops._safe_str(value).strip()

    return ""

def _ops_new_outbound_message_id(selected_id: int) -> str:
    """
    Create a unique message ID for outbound replies recorded in order_intake.

    Do not use only tms-reply-{selected_id}; that causes duplicate-key errors
    when the dispatcher sends or records more than one reply for the same work item.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"tms-reply-{selected_id}-{timestamp}-{uuid4().hex[:8]}"


def build_control_level_counts(inbox_df: pd.DataFrame | None) -> dict[str, int]:
    """Small plain-dict snapshot of inbox_df's control_level counts, built
    while inbox_df is still in the caller's scope. The selected-item dialog
    renders in its own top-level function and must never reference inbox_df
    directly - it only ever sees this derived payload."""
    if not isinstance(inbox_df, pd.DataFrame) or "control_level" not in inbox_df.columns:
        return {}
    return {
        str(level): int(count)
        for level, count in inbox_df["control_level"].dropna().value_counts().to_dict().items()
    }


def build_dispatcher_queue_counts(inbox_df: pd.DataFrame | None) -> dict[str, int]:
    if not isinstance(inbox_df, pd.DataFrame) or "dispatcher_queue" not in inbox_df.columns:
        return {}
    return {
        str(queue): int(count)
        for queue, count in inbox_df["dispatcher_queue"].dropna().value_counts().to_dict().items()
    }


def count_critical_priority(inbox_df: pd.DataFrame | None) -> int:
    if not isinstance(inbox_df, pd.DataFrame) or "priority_label" not in inbox_df.columns:
        return 0
    return int(inbox_df["priority_label"].eq("Critical").sum())


ORDER_DRAFT_FIELD_SOURCES = {
    "Customer": ("customer", "Customer", "customer_hint"),
    "TYPE": ("service_flow", "TYPE", "Service Flow"),
    "Booking Number": ("booking_number", "Booking Number", "booking_hint"),
    "Reference Number": ("reference_number", "Reference Number", "reference_hint"),
    "Container Number": ("container_number", "Container Number", "container_hint"),
    "Size": ("container_size", "Size", "size_hint"),
    "Port": ("origin_port", "Port", "port_hint"),
    "Terminal": ("terminal", "Terminal", "terminal_hint"),
    "Warehouse": ("destination_warehouse", "Warehouse", "warehouse_hint"),
    "Pickup Address": ("pickup_address", "Pickup Address", "pickup_address_hint"),
    "Address": ("delivery_address", "Address", "delivery_hint"),
    "Delivery Need Date": ("delivery_need_date", "Delivery Need Date", "delivery_need_date_hint"),
    "Document Cutoff": ("document_cutoff", "Document Cutoff", "document_cutoff_hint"),
    "LFD": ("lfd", "LFD", "lfd_hint"),
    "Port PIN": ("port_pin", "Port PIN", "port_pin_hint"),
    "Dispatcher Notes": ("dispatcher_notes", "Dispatcher Notes", "action_required"),
}


def build_order_draft_projection(
    *,
    record,
    parsed_data: dict | None,
    pending_draft: dict | None = None,
    attachment_fields: dict | None = None,
    email_fields: dict | None = None,
    tokens: dict | None = None,
) -> dict:
    """Build the single editable/order-creation projection in authority order."""
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})
    parsed = dict(parsed_data or {})
    pending = dict(pending_draft or {})
    document = dict(attachment_fields or {})
    email = dict(email_fields or parsed.get("_email_parsed") or {})
    token_values = dict(tokens or {})
    projected: dict[str, str] = {}

    for field, (draft_key, parsed_key, hint_key) in ORDER_DRAFT_FIELD_SOURCES.items():
        value = (
            ops._safe_str(pending.get(draft_key))
            or ops._safe_str(parsed.get(parsed_key))
            or ops._safe_str(document.get(parsed_key))
            or ops._safe_str(email.get(parsed_key))
            or ops._safe_str(record_dict.get(hint_key))
        )
        if not value:
            token_key = {
                "Booking Number": "booking_number",
                "Reference Number": "reference_number",
                "Container Number": "container_number",
            }.get(field)
            if token_key:
                value = ops._safe_str(token_values.get(token_key))
        projected[field] = value

    projected["TYPE"] = normalize_service_flow(
        projected.get("TYPE", ""),
        default=projected.get("TYPE", ""),
    )
    return projected


def should_show_order_draft_review(
    record,
    parsed_data: dict | None,
    attachments: list[dict] | None = None,
    pending_draft: dict | None = None,
    body: str = "",
    final_decision: dict | None = None,
) -> bool:
    """Return true for actionable New Orders even when required fields are blank."""
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})
    decision = dict(final_decision or {})
    parsed = dict(parsed_data or {})
    identity = " ".join(
        [
            ops._safe_str(record_dict.get("request_type_clean")),
            ops._safe_str(record_dict.get("request_type")),
            ops._safe_str(record_dict.get("work_queue")),
            ops._safe_str(record_dict.get("dispatcher_queue")),
            ops._safe_str(decision.get("work_type")),
            ops._safe_str(decision.get("queue")),
        ]
    ).lower()
    update_identity = any(
        phrase in identity
        for phrase in ("booking update", "existing load", "appointment", "billing", "document")
    )
    new_order_identity = (
        "new order" in identity
        or "new booking" in identity
        or "possible new booking" in identity
        or "pending order" in identity
        or ops._safe_str(parsed.get("TYPE")) in {
            "Import",
            "Export",
            "Local Import",
            "Local Export",
        }
        and ("new" in identity or not identity.strip())
    )
    if update_identity or not new_order_identity:
        return False

    evidence_fields = (
        "Customer",
        "TYPE",
        "Booking Number",
        "Reference Number",
        "Container Number",
        "Warehouse",
        "Port",
    )
    has_structured_evidence = any(ops._safe_str(parsed.get(field)) for field in evidence_fields)
    has_pending = any(ops._safe_str(value) for value in dict(pending_draft or {}).values())
    has_order_language = bool(
        re.search(
            r"\b(?:new\s+booking|booking|new\s+order|arrange|dispatch\s+order|"
            r"local\s+(?:outbound|inbound|export|import)|transfer)\b",
            ops._safe_str(body),
            re.I,
        )
    )
    return bool(has_structured_evidence or attachments or has_pending or has_order_language)


QUEUE_SORT_COLUMNS = {
    "Received": "source_received_at",
    "Customer": "customer_hint",
    "Service Flow": "Service Flow",
    "Queue": "dispatcher_queue",
    "Subject / Summary": "source_subject",
    "Booking": "booking_hint",
    "Container": "container_hint",
}


def sort_operations_queue(
    frame: pd.DataFrame,
    *,
    sort_by: str = "Received",
    direction: str = "Descending",
) -> pd.DataFrame:
    """Stable queue sort with datetime handling and blanks always last."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    column = QUEUE_SORT_COLUMNS.get(sort_by, "source_received_at")
    if column not in frame.columns and sort_by == "Received" and "email_received" in frame.columns:
        column = "email_received"
    if column not in frame.columns:
        return frame.copy()

    sorted_frame = frame.copy()
    key_column = "__operations_sort_value"
    blank_column = "__operations_sort_blank"
    if sort_by == "Received":
        sorted_frame[key_column] = pd.to_datetime(sorted_frame[column], errors="coerce", utc=True)
        sorted_frame[blank_column] = sorted_frame[key_column].isna()
    else:
        text_values = sorted_frame[column].fillna("").astype(str).str.strip()
        sorted_frame[blank_column] = text_values.eq("")
        sorted_frame[key_column] = text_values.str.casefold()

    ascending = str(direction).lower().startswith("asc")
    sorted_frame = sorted_frame.sort_values(
        [blank_column, key_column],
        ascending=[True, ascending],
        kind="mergesort",
        na_position="last",
    )
    return sorted_frame.drop(columns=[blank_column, key_column])


def queue_display_value(value) -> str:
    clean = ops._safe_str(value).strip()
    return "—" if not clean or clean in {"•", "â€¢", "·", "-", "—"} else clean


def _load_pending_order_draft_projection(conversation_key: str) -> dict:
    if not ops._safe_str(conversation_key):
        return {}
    try:
        draft_df = ops.read_df(
            """
            select
                customer,
                booking_number,
                reference_number,
                container_number,
                service_flow,
                origin_port,
                destination_warehouse,
                delivery_address,
                delivery_need_date,
                lfd,
                document_cutoff,
                container_size,
                terminal,
                dispatcher_notes
            from public.order_intake_drafts
            where (
                    conversation_key = :conversation_key
                 or booking_number = :conversation_key
                 or container_number = :conversation_key
            )
            order by dispatcher_confirmed_at desc nulls last, updated_at desc
            limit 1
            """,
            {"conversation_key": conversation_key},
        )
    except Exception:
        return {}
    return draft_df.iloc[0].to_dict() if draft_df is not None and not draft_df.empty else {}


def render_operations_inbox() -> None:
    initialize_work_item_state(st.session_state)
    queue_rerender_started = (
        time.perf_counter()
        if st.session_state.get(WORK_ITEM_OPEN_STARTED_AT) is not None
        else None
    )
    st.markdown(
        """
        <div class="ops-header">
            <div class="ops-kicker">Operations</div>
            <div class="ops-title">Operations Control Center</div>
            <div class="ops-subtitle">
                Manage work, not just email: triage operational cases, business communications, and archive/no-action messages from one dispatch workspace.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        ops.ensure_operations_email_sync_schema()
    except Exception as exc:
        st.warning(f"Email sync schema is not ready yet: {exc}")
        st.caption(f"Database config source: {get_config_source('DATABASE_URL')}")

    try:
        sync_metrics = ops._operations_email_sync_metrics()
    except Exception:
        sync_metrics = {}

    c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 1.2, 1.0, 1.4])

    with c1:
        sync_running = bool(st.session_state.get("operations_email_sync_running", False))

        if st.button(
            "Sync Email Engine",
            key="operations_sync_email_engine",
            use_container_width=True,
            disabled=sync_running,
        ):
            sync_completed = False
            st.session_state["operations_email_sync_running"] = True

            try:
                with st.spinner("Checking the most recent operations emails..."):
                    sync_result = ops.sync_operations_email_engine(
                        limit=8,
                        time_budget_seconds=25,
                    )

                st.session_state["operations_email_import_result"] = sync_result
                sync_completed = True

            except Exception as exc:
                st.session_state["operations_email_import_result"] = {
                    "error": str(exc),
                    "fetched": 0,
                    "imported": 0,
                    "skipped": 0,
                    "errors": 1,
                }
                st.error(f"Could not synchronize email: {exc}")

            finally:
                st.session_state["operations_email_sync_running"] = False

            if sync_completed:
                close_work_item(st.session_state)
                ops.refresh_data()
                st.rerun()

    with c2:
        if st.button("Refresh Inbox", key="operations_refresh_inbox", width="stretch"):
            close_work_item(st.session_state)
            ops.refresh_data()
            st.rerun()

    with c3:
        if st.button("Recheck Next Batch", key="operations_recheck_smart_groups", use_container_width=True):
            with st.spinner("Running fast triage on the next small batch..."):
                triage_result = ops.auto_classify_open_inbox_items(limit=25, time_budget_seconds=8)
                st.session_state["operations_smart_group_update_result"] = triage_result
                close_work_item(st.session_state)
                st.rerun()

    with c4:
        st.caption("Last Sync")
        st.write(sync_metrics.get("last_sync") or "-")

    with c5:
        selected_flow = render_service_flow_filter("operations_inbox_service_flow")

    completed_action = st.session_state.pop("operations_last_completed_action", None)

    if completed_action:
        st.success(
            f"Email action completed. Status: {completed_action.get('status', '-')}. "
            f"Sent to: {completed_action.get('recipient', '-')}. "
            "The work item was removed from the active review workspace."
        )
    _queue_query_started = time.perf_counter()
    try:
        where_clause = ops._inbox_review_where_clause()
        inbox_df = ops._load_operations_inbox_df(where_clause)
    except Exception as exc:
        st.error(f"Could not load Operations Inbox: {exc}")
        st.caption(f"Database config source: {get_config_source('DATABASE_URL')}")
        st.info("If this is the first time using Operations Inbox email, run database/operations_email_workflow_migration.sql in Supabase.")
        return
    queue_query_ms = round((time.perf_counter() - _queue_query_started) * 1000, 2)

    if inbox_df.empty:
        st.success("No open customer requests.")
        ops._render_no_open_inbox_explanation()
        return

    if "raw_text_preview" in inbox_df.columns:
        inbox_df["raw_text_preview"] = inbox_df["raw_text_preview"].fillna("").astype(str).apply(extract_latest_email_body)

    inbox_df["conversation_join_key"] = inbox_df.apply(ops._row_conversation_join_key, axis=1)
    conversation_summary = ops._load_operations_conversation_summary_df()
    if not conversation_summary.empty:
        inbox_df = inbox_df.merge(
            conversation_summary,
            on="conversation_join_key",
            how="left",
        )
    for column in ["latest_direction", "latest_conversation_status", "conversation_message_count"]:
        if column not in inbox_df.columns:
            inbox_df[column] = ""
    inbox_df["latest_direction"] = inbox_df["latest_direction"].fillna(inbox_df["email_direction"].fillna("inbound"))
    inbox_df["reply_status"] = inbox_df["latest_conversation_status"].fillna(inbox_df["conversation_status"].fillna("New Conversation"))
    inbox_df["conversation_message_count"] = pd.to_numeric(
        inbox_df["conversation_message_count"],
        errors="coerce",
    ).fillna(1).astype(int)
    inbox_df["last_message_at"] = pd.to_datetime(
        inbox_df.get("last_message_at", pd.Series(dtype=str)),
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")

    inbox_df["request_type_clean"] = (
        inbox_df["request_type"]
                .fillna("Needs Classification")
                .astype(str)
                .str.strip()
        )
    inbox_df["request_type_clean"] = inbox_df.apply(ops._effective_operations_request_type_for_row, axis=1)
    inbox_df["request_type"] = inbox_df["request_type_clean"]
    inbox_df["confidence_score"] = pd.to_numeric(
        inbox_df["confidence_score"],
        errors="coerce",
    ).fillna(0).astype(int)
    attachment_count_source = inbox_df["attachment_count"] if "attachment_count" in inbox_df.columns else pd.Series(0, index=inbox_df.index)
    source_attachment_count_source = (
        inbox_df["source_attachment_count"] if "source_attachment_count" in inbox_df.columns else pd.Series(0, index=inbox_df.index)
    )
    inbox_df["attachment_count"] = pd.to_numeric(
        attachment_count_source,
        errors="coerce",
    ).fillna(0).astype(int)
    inbox_df["source_attachment_count"] = pd.to_numeric(
        source_attachment_count_source,
        errors="coerce",
    ).fillna(0).astype(int)
    inbox_df["attachment_status"] = inbox_df.apply(ops._operations_attachment_status_for_row, axis=1)

    def extract_reference_from_text(text: str) -> str:
        tokens = ops._extract_reference_tokens(text)
        return (
            tokens.get("booking_number")
            or tokens.get("container_number")
            or tokens.get("reference_number")
            or ""
        )

    def extract_requested_time(text: str) -> str:
        text = str(text)
        range_match = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
            text,
            re.I,
        )
        if range_match:
            return ops._safe_str(range_match.group(1))
        match = re.search(r"\b(?:at\s*)?(\d{3,4})\s*(?:on\s*)?(\d{1,2}/\d{1,2})?\b", text, re.I)
        if match:
            time_part = match.group(1)
            date_part = match.group(2) or ""
            return f"{time_part} {date_part}".strip()
        return ""

    inbox_df["client_name"] = (
        inbox_df["source_sender"]
        .fillna("")
        .astype(str)
        .str.extract(r"^([^<]+)")[0]
        .fillna("")
        .str.strip()
    )

    inbox_df["email_received"] = pd.to_datetime(
        inbox_df["source_received_at"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")

    inbox_df["reference_hint"] = inbox_df["conversation_key"].fillna("").astype(str)
    generic_reference = inbox_df["reference_hint"].str.lower().isin(["", "customer-request"]) | inbox_df["reference_hint"].str.lower().str.startswith(("email-", "intake-"))
    inbox_df.loc[generic_reference, "reference_hint"] = (
        inbox_df.loc[generic_reference, "source_subject"].fillna("").apply(ops.extract_reference_from_text)
    )
    inbox_df["requested_time"] = ""
    inbox_df["review_status_clean"] = (
        inbox_df["review_status"]
                .fillna("Open")
                .astype(str)
                .str.strip()
        )
    inbox_df["owner_label"] = inbox_df.apply(ops._operations_owner_label_for_row, axis=1)
    inbox_df["priority_label"] = inbox_df.apply(ops._operations_priority_label_for_row, axis=1)
    inbox_df["status_label"] = inbox_df.apply(ops._operations_status_label_for_row, axis=1)
    inbox_df["work_queue"] = inbox_df.apply(ops._operations_work_queue_for_row, axis=1)
    inbox_df["case_total_messages"] = pd.to_numeric(
        inbox_df.get("case_message_count", inbox_df.get("conversation_message_count", 1)),
        errors="coerce",
    ).fillna(inbox_df["conversation_message_count"]).fillna(1).astype(int)
    wait_started = pd.to_datetime(
        inbox_df["case_customer_wait_started_at"].fillna(inbox_df["case_department_wait_started_at"])
        if "case_customer_wait_started_at" in inbox_df.columns and "case_department_wait_started_at" in inbox_df.columns
        else pd.Series(pd.NaT, index=inbox_df.index),
        errors="coerce",
        utc=True,
    )
    now_utc = pd.Timestamp.now(tz="UTC")
    inbox_df["waiting_hours"] = ((now_utc - wait_started).dt.total_seconds() / 3600).fillna(0).round(1)
    inbox_df = ops._collapse_operations_inbox_to_cases(inbox_df)
    if not inbox_df.empty:
        inbox_df["control_level"] = inbox_df.apply(ops._operations_control_level_for_row, axis=1)
        inbox_df["department_lane"] = inbox_df.apply(ops._operations_department_lane_for_row, axis=1)
        inbox_df["work_item"] = inbox_df.apply(ops._operations_work_item_for_row, axis=1)
        inbox_df["recommended_action"] = inbox_df.apply(ops._operations_recommended_action_for_row, axis=1)
        inbox_df["control_reason"] = inbox_df.apply(ops._operations_control_reason_for_row, axis=1)
        inbox_df["confidence_label"] = inbox_df["confidence_score"].apply(ops._operations_confidence_label)
        identifier_hints = inbox_df.apply(ops._operations_identifier_hints_for_row, axis=1)
        inbox_df["booking_hint"] = identifier_hints.apply(lambda value: value.get("booking", "") if isinstance(value, dict) else "")
        inbox_df["container_hint"] = identifier_hints.apply(lambda value: value.get("container", "") if isinstance(value, dict) else "")
        inbox_df["customer_hint"] = identifier_hints.apply(lambda value: value.get("customer", "") if isinstance(value, dict) else "")
        blank_customer = inbox_df["customer_hint"].fillna("").astype(str).str.strip().eq("")
        if blank_customer.any():
            inbox_df.loc[blank_customer, "customer_hint"] = inbox_df.loc[blank_customer, "client_name"].fillna("")
        inbox_df["warehouse_hint"] = identifier_hints.apply(lambda value: value.get("warehouse", "") if isinstance(value, dict) else "")
        inbox_df["port_hint"] = identifier_hints.apply(lambda value: value.get("port", "") if isinstance(value, dict) else "")
        inbox_df["delivery_hint"] = identifier_hints.apply(lambda value: value.get("delivery", "") if isinstance(value, dict) else "")
        inbox_df["load_hint"] = inbox_df["matched_load_id"].fillna("").astype(str).replace({"nan": "", "None": ""})
        inbox_df["dispatcher_queue"] = inbox_df.apply(_dispatcher_queue_for_row, axis=1)

    inbox_df = apply_service_flow_filter(inbox_df, selected_flow)
    if "dispatcher_queue" not in inbox_df.columns:
        inbox_df["dispatcher_queue"] = pd.Series(dtype="object")
    
    with st.expander("Operations Inbox Process Feedback", expanded=False):
        st.markdown(
            """
- Level 1 is dispatcher work: new bookings, booking revisions, appointment changes, driver issues, port issues, PODs, documents, and customer load questions.
- Level 2 is important business communication: billing, insurance, legal, vendor, sales, HR, safety, and management work that should not distract dispatch.
- Level 3 is no-action or archive work: spam, marketing, newsletters, duplicates, FYI messages, and other mail that should stay searchable but not become a load.
- `Needs Review` is only for uncertainty. Once a dispatcher classifies a sender/topic, future messages should route faster.
- Press Open on a queue row to review the source email, attachments, order draft, and business actions.
            """.strip()
        )
    with st.expander("Control Filters", expanded=False):
        p1, p2 = st.columns([1, 2])

        with p1:
            perspective_filter = st.selectbox(
                "Perspective",
                ["Operations Control Center", "Dispatch", "Accounting", "Management", "Customer Service"],
                index=0,
                key="operations_case_perspective_filter",
            )

        with p2:
            manager_focus = st.selectbox(
                "Manager Focus",
                ["All Open Cases", "High Priority", "Escalated / Overdue", "Waiting >24 Hours", "Unassigned"],
                index=0,
                key="operations_manager_focus_filter",
                disabled=perspective_filter != "Management",
            )

        f1, f2, f3, f4 = st.columns([2, 1, 1, 1])

        with f1:
            search_filter = st.text_input(
                "Search",
                value="",
                placeholder="Booking, container, customer, sender, subject, or reference",
                key="operations_inbox_search_filter",
            )

        with f2:
            owner_values = pd.concat(
                [
                    inbox_df["owner_label"].dropna().astype(str),
                    inbox_df["department_lane"].dropna().astype(str),
                ],
                ignore_index=True,
            )
            owner_options = ["All Owners"] + sorted(
                [value for value in owner_values.unique() if value]
            )
            owner_filter = st.selectbox(
                "Owner / Department",
                owner_options,
                index=0,
                key="operations_owner_queue_filter",
            )

        with f3:
            priority_options = ["All Priorities", "Critical", "High", "Medium", "Low"]
            priority_filter = st.selectbox(
                "Priority",
                priority_options,
                index=0,
                key="operations_priority_filter",
            )

        with f4:
            attachment_filter = st.selectbox(
                "Attachments",
                ["All Attachments", "Saved", "Mailbox", "Missing", "Mentioned", "None"],
                index=0,
                key="operations_attachment_filter",
            )

        s1, s2 = st.columns(2)

        with s1:
            status_options = ["All Statuses"] + sorted(
                [value for value in inbox_df["status_label"].dropna().astype(str).unique() if value]
            )
            status_filter = st.selectbox(
                "Status",
                status_options,
                index=0,
                key="operations_status_filter",
            )

        with s2:
            type_options = ["All Request Types"] + sorted(
                [value for value in inbox_df["request_type_clean"].dropna().astype(str).unique() if value]
            )
            request_type_filter = st.selectbox(
                "Request Type",
                type_options,
                index=0,
                key="operations_request_type_filter",
            )

    filtered_df = inbox_df.copy()
    if perspective_filter == "Dispatch":
        filtered_df = filtered_df[
            filtered_df["control_level"].isin(["Level 1 - Operational Cases", "Needs Review"])
            & filtered_df["department_lane"].isin(["Dispatch", "Operations", "Customer Service", "Human Review"])
        ].copy()
    elif perspective_filter == "Accounting":
        filtered_df = filtered_df[
            filtered_df["department_lane"].eq("Accounting")
            | filtered_df["owner_label"].eq("Billing")
            | filtered_df["request_type_clean"].eq("Billing")
        ].copy()
    elif perspective_filter == "Customer Service":
        filtered_df = filtered_df[
            filtered_df["department_lane"].eq("Customer Service")
            | filtered_df["owner_label"].eq("Customer")
            | filtered_df["request_type_clean"].isin(["Customer Request", "Missing Information"])
        ].copy()
    elif perspective_filter == "Management":
        if manager_focus == "High Priority":
            filtered_df = filtered_df[filtered_df["priority_label"].isin(["Critical", "High"])].copy()
        elif manager_focus == "Escalated / Overdue":
            filtered_df = filtered_df[
                filtered_df["priority_label"].eq("Critical")
                | filtered_df["case_sla_status"].fillna("").astype(str).str.contains("Overdue|Warning", case=False, na=False)
            ].copy()
        elif manager_focus == "Waiting >24 Hours":
            filtered_df = filtered_df[filtered_df["waiting_hours"].ge(24)].copy()
        elif manager_focus == "Unassigned":
            filtered_df = filtered_df[
                filtered_df["owner_label"].isin(["", "Unassigned"])
                | filtered_df["case_owner"].fillna("").astype(str).isin(["", "Unassigned"])
            ].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "id",
            "control_level",
            "department_lane",
            "work_queue",
            "dispatcher_queue",
            "work_item",
            "recommended_action",
            "client_name",
            "customer_hint",
            "booking_hint",
            "container_hint",
            "source_sender",
            "source_subject",
            "raw_text_preview",
            "reference_hint",
            "warehouse_hint",
            "port_hint",
            "delivery_hint",
            "conversation_key",
            "email_thread_id",
            "case_number",
            "matched_load_id",
            "action_required",
            "Service Flow",
        ]
        available_search_columns = [column for column in searchable_columns if column in filtered_df.columns]
        search_blob = filtered_df[available_search_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        search_mask = pd.Series(True, index=filtered_df.index)
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            search_mask &= search_blob.str.contains(re.escape(term), na=False)
        filtered_df = filtered_df[search_mask].copy()
    if owner_filter != "All Owners":
        filtered_df = filtered_df[
            filtered_df["owner_label"].eq(owner_filter) | filtered_df["department_lane"].eq(owner_filter)
        ].copy()
    if priority_filter != "All Priorities":
        filtered_df = filtered_df[filtered_df["priority_label"].eq(priority_filter)].copy()
    if attachment_filter != "All Attachments":
        if attachment_filter == "None":
            filtered_df = filtered_df[filtered_df["attachment_status"].eq("None")].copy()
        else:
            filtered_df = filtered_df[filtered_df["attachment_status"].str.startswith(attachment_filter, na=False)].copy()
    if status_filter != "All Statuses":
        filtered_df = filtered_df[filtered_df["status_label"].eq(status_filter)].copy()
    if request_type_filter != "All Request Types":
        filtered_df = filtered_df[filtered_df["request_type_clean"].eq(request_type_filter)].copy()

    if filtered_df.empty:
        st.info("No Operations Inbox items match the current filters.")

    display_column_labels = {
        "email_received": "Received",
        "customer_hint": "Customer",
        "Service Flow": "Service Flow",
        "dispatcher_queue": "Queue",
        "source_subject": "Subject / Summary",
        "booking_hint": "Booking",
        "container_hint": "Container",
        "recommended_action": "Recommended Action",
        "priority_label": "Priority",
        "status_label": "Status",
    }
    shared_display_cols = [
        "email_received",
        "customer_hint",
        "Service Flow",
        "dispatcher_queue",
        "source_subject",
        "booking_hint",
        "container_hint",
        "recommended_action",
        "priority_label",
        "status_label",
    ]

    # Hide completed/outbound/stale conversation rows before building queue counts.
    filtered_df = _ops_filter_active_conversation_work_items(filtered_df)

    sort_col, direction_col = st.columns([2, 1])
    with sort_col:
        sort_by = st.selectbox(
            "Sort by",
            list(QUEUE_SORT_COLUMNS),
            index=0,
            key="operations_queue_sort_by",
        )
    with direction_col:
        sort_direction = st.segmented_control(
            "Direction",
            ["Descending", "Ascending"],
            default="Descending",
            key="operations_queue_sort_direction",
            width="stretch",
        ) or "Descending"

    visible_review_ids: set[int] = set()
    queue_titles = {
        queue: f"{queue} ({int(filtered_df['dispatcher_queue'].eq(queue).sum())})"
        for queue in DISPATCHER_QUEUE_ORDER
    }
        
    queue_label_to_value = {label: queue for queue, label in queue_titles.items()}
    selected_queue_label = st.radio(
        "Operations Queue",
        list(queue_label_to_value.keys()),
        horizontal=True,
        key="operations_dispatcher_queue",
    )
    selected_queue = queue_label_to_value[selected_queue_label]
    tab_df = pd.DataFrame()
    active_display_cols = [column for column in shared_display_cols if column in filtered_df.columns]
    if filtered_df.empty:
        st.info("No work items match the current filters.")
    else:
        tab_df = filtered_df[filtered_df["dispatcher_queue"].eq(selected_queue)].copy()
        visible_review_ids = set(tab_df["id"].dropna().astype(int).tolist()) if "id" in tab_df.columns else set()
        if "customer_hint" in tab_df.columns and "client_name" in tab_df.columns:
            blank_customer = tab_df["customer_hint"].fillna("").astype(str).str.strip().eq("")
            if blank_customer.any():
                tab_df.loc[blank_customer, "customer_hint"] = tab_df.loc[blank_customer, "client_name"].fillna("")
        tab_df = sort_operations_queue(
            tab_df,
            sort_by=sort_by,
            direction=sort_direction,
        )

    if tab_df.empty:
        st.caption("0 work item(s)")
        st.info(f"No {selected_queue.lower()} work items.")
    else:
        # Server-side pagination of the render, not just a caption: only
        # the current page's rows get an Open button. Filtering/sorting
        # above this point still runs over the full queue (that pipeline
        # is Python/Pandas-based business logic this phase does not
        # redesign - see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md,
        # "Known limitations"); only the render step is now bounded.
        total_items = len(tab_df)
        page_size_col, page_info_col, prev_col, next_col = st.columns([1, 2.4, 0.6, 0.6])
        with page_size_col:
            page_size = st.selectbox(
                "Page size",
                [10, 25, 50],
                index=1,
                key="operations_queue_page_size",
            )
        total_pages = max(math.ceil(total_items / page_size), 1)
        page_state_key = f"operations_queue_page__{selected_queue}"
        current_page = min(max(int(st.session_state.get(page_state_key, 1)), 1), total_pages)
        with prev_col:
            if st.button(
                "< Prev",
                key=f"operations_queue_prev_{selected_queue}",
                disabled=current_page <= 1,
                use_container_width=True,
            ):
                current_page -= 1
        with next_col:
            if st.button(
                "Next >",
                key=f"operations_queue_next_{selected_queue}",
                disabled=current_page >= total_pages,
                use_container_width=True,
            ):
                current_page += 1
        st.session_state[page_state_key] = current_page

        page_start_index = (current_page - 1) * page_size
        page_df = tab_df.iloc[page_start_index : page_start_index + page_size]

        with page_info_col:
            page_first_item = page_start_index + 1
            page_last_item = min(page_start_index + page_size, total_items)
            st.caption(
                f"Showing {page_first_item}-{page_last_item} of {total_items} work item(s) "
                f"- page {current_page} of {total_pages}"
            )

        st.markdown(
            """
            <style>
            .st-key-operations_queue_header {
                background: color-mix(in srgb, var(--secondary-background-color) 88%, transparent);
                border-radius: 0.5rem;
                font-weight: 600;
            }
            [class*="st-key-operations_queue_row_"] [data-testid="stColumn"] + [data-testid="stColumn"],
            .st-key-operations_queue_header [data-testid="stColumn"] + [data-testid="stColumn"] {
                border-left: 1px solid color-mix(in srgb, var(--text-color) 16%, transparent);
                padding-left: 0.65rem;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        queue_columns = [
            ("email_received", "Received", 1.35),
            ("customer_hint", "Customer", 1.5),
            ("Service Flow", "Service Flow", 0.85),
            ("dispatcher_queue", "Queue", 1.05),
            ("source_subject", "Subject / Summary", 2.4),
            ("booking_hint", "Booking", 1.15),
            ("container_hint", "Container", 1.15),
        ]
        with st.container(
            border=True,
            key="operations_queue_header",
        ):
            header = st.columns(
                [width for _, _, width in queue_columns] + [0.65],
                vertical_alignment="center",
            )
            for column, (_, label, _) in zip(header, queue_columns):
                column.caption(label)
            header[-1].caption("Action")

        for _, queue_row in page_df.iterrows():
            work_item_id = int(queue_row["id"])
            with st.container(
                border=True,
                key=f"operations_queue_row_{work_item_id}",
            ):
                row_columns = st.columns(
                    [width for _, _, width in queue_columns] + [0.65],
                    vertical_alignment="center",
                )
                for column, (field, _, _) in zip(row_columns, queue_columns):
                    value = queue_display_value(queue_row.get(field, ""))
                    if field == "source_subject":
                        value = _ops_compact_subject(value, max_len=72)
                    column.write(value)
                row_columns[-1].button(
                    "Open",
                    key=f"open_work_item_{work_item_id}",
                    width="stretch",
                    on_click=open_work_item,
                    args=(st.session_state,),
                    kwargs={
                        "work_item_id": work_item_id,
                        "queue_name": selected_queue,
                    },
                )

    st.divider()
    selected_id = st.session_state.get("selected_work_item_id")
    selected_tab_name = st.session_state.get("selected_work_item_queue")

    if selected_id is None or not st.session_state.get(WORK_ITEM_DIALOG_OPEN, False):
        st.info("Press Open on a work item to review the email, order draft, and available actions.")
        return

    if int(selected_id) not in visible_review_ids:
        close_work_item(st.session_state)
        st.info("The selected work item is no longer in this queue.")
        return

    # inbox_df is only in scope in this function. The selected-item dialog
    # renders in its own top-level function (so it can be opened via
    # st.dialog), so it must receive these as small explicit payloads
    # rather than reaching back for inbox_df, which does not exist there.
    level_counts_payload = build_control_level_counts(inbox_df)
    queue_counts_payload = build_dispatcher_queue_counts(inbox_df)
    critical_priority_count = count_critical_priority(inbox_df)

    def _close_operations_work_item_dialog() -> None:
        close_work_item(st.session_state)

    dialog_subject = ""
    if "source_subject" in tab_df.columns and not tab_df.empty:
        subject_matches = tab_df.loc[tab_df["id"] == selected_id, "source_subject"]
        if not subject_matches.empty:
            dialog_subject = ops._safe_str(subject_matches.iloc[0])
    dialog_title = f"Work Item #{selected_id}"
    if dialog_subject:
        dialog_title += f" - {dialog_subject[:60]}"

    if queue_rerender_started is not None:
        full_queue_rerender_ms = round((time.perf_counter() - queue_rerender_started) * 1000, 2)
        st.session_state["_operations_open_prerender_stages"] = {
            "queue_query": queue_query_ms,
            # Transform (classification/sort/pagination slice) and render
            # (drawing the queue table) aren't split further yet - see
            # docs/architecture/BACKEND_BOUNDARY_PHASE_1.md, "Known
            # limitations". This is everything after the DB query returns.
            "queue_transform_and_render": round(full_queue_rerender_ms - queue_query_ms, 2),
            "full_queue_rerender": full_queue_rerender_ms,
        }

    @st.dialog(dialog_title, width="large", on_dismiss=_close_operations_work_item_dialog)
    def _operations_work_item_dialog(
        selected_id: int,
        selected_tab_name: str,
        level_counts_payload: dict[str, int] | None = None,
        queue_counts_payload: dict[str, int] | None = None,
        critical_priority_count: int = 0,
    ) -> None:
        size_labels = list(DIALOG_SIZE_PRESETS.keys())
        current_size = st.session_state.get("operations_inbox_dialog_size", "Large")
        if current_size not in DIALOG_SIZE_PRESETS:
            current_size = "Large"
        header_cols = st.columns([2, 1])
        with header_cols[0]:
            if st.button("Back to Queue", key="clear_operations_inbox_selection", use_container_width=True):
                _close_operations_work_item_dialog()
                st.rerun()
        with header_cols[1]:
            selected_size = st.radio(
                "Popup size",
                size_labels,
                index=size_labels.index(current_size),
                horizontal=True,
                key="operations_inbox_dialog_size",
                label_visibility="collapsed",
            )
        dialog_width, dialog_height = DIALOG_SIZE_PRESETS[selected_size]
        st.markdown(
            '<style>div[data-testid="stDialog"]{width:' + dialog_width + ' !important;height:' + dialog_height + ' !important;}</style>',
            unsafe_allow_html=True,
        )
        _render_selected_operations_work_item(
            int(selected_id),
            selected_tab_name,
            level_counts_payload=level_counts_payload,
            queue_counts_payload=queue_counts_payload,
            critical_priority_count=critical_priority_count,
        )

    _operations_work_item_dialog(
        int(selected_id),
        selected_tab_name,
        level_counts_payload,
        queue_counts_payload,
        critical_priority_count,
    )


def _render_selected_operations_work_item(
    selected_id: int,
    selected_tab_name: str,
    level_counts_payload: dict[str, int] | None = None,
    queue_counts_payload: dict[str, int] | None = None,
    critical_priority_count: int = 0,
) -> None:
    initial_open_timing = st.session_state.get(WORK_ITEM_OPEN_STARTED_AT) is not None
    timing = WorkItemOpenTiming(
        work_item_id=int(selected_id),
        started_at=st.session_state.get(WORK_ITEM_OPEN_STARTED_AT),
    )
    timing.stages_ms.update(
        st.session_state.pop("_operations_open_prerender_stages", {})
    )
    timing.mark_elapsed("button_to_dialog_render")
    with timing.stage("work_item_database_fetch"):
        record_df = _load_operations_inbox_record(int(selected_id))

    if record_df.empty:
        st.warning("Selected request was not found.")
        if initial_open_timing:
            timing.finish(state=st.session_state, logger=logger)
            st.session_state.pop(WORK_ITEM_OPEN_STARTED_AT, None)
        return

    st.markdown(f"### Review Work Item - {selected_tab_name} - Request #{selected_id}")
    st.divider()

    record = record_df.iloc[0]
    with timing.stage("parsed_data_loading"):
        parsed = _coerce_json_dict(record.get("parsed_data"))
    record["request_type_clean"] = _effective_operations_request_type_for_row(record)
    record["review_status_clean"] = _safe_str(record.get("review_status", "Open")) or "Open"
    record["owner_label"] = _operations_owner_label_for_row(record)
    record["priority_label"] = _operations_priority_label_for_row(record)
    record["status_label"] = _operations_status_label_for_row(record)
    record["work_queue"] = _operations_work_queue_for_row(record)
    record["control_level"] = _operations_control_level_for_row(record)
    record["department_lane"] = _operations_department_lane_for_row(record)
    record["work_item"] = _operations_work_item_for_row(record)
    record["recommended_action"] = _operations_recommended_action_for_row(record)
    record["dispatcher_queue"] = _dispatcher_queue_for_row(record)

    subject = str(record.get("source_subject", "") or "")
    sender = str(record.get("source_sender", "") or "")
    body = str(record.get("raw_text", "") or "")
    body = extract_latest_email_body(body) or body
    # Parsing, attachment reconciliation, and parsed_data persistence happen
    # during ingestion. Opening a work item consumes that persisted result.
    pending_reply_updated_fields = {}

    # This is a new booking email, not an existing update.
    if _ops_has_true_new_order_signal(subject, body, record) and not _ops_has_reply_or_update_signal(subject, body, record):
        record["request_type"] = "New Booking"
        record["request_type_clean"] = "New Booking"
        record["work_queue"] = "New Orders"
        record["dispatcher_queue"] = "New Orders"

    with timing.stage("classification_and_saved_load_match"):
        classification = ops._operations_classification_for_review(
            record,
            parsed,
            subject,
            body,
            fallback_key=f"intake-{selected_id}",
        )

    detected_type = (
        classification.get("request_type")
        or record.get("request_type_clean")
        or record.get("request_type")
        or "Needs Classification"
    )

    tokens = classification.get("tokens") or ops._extract_reference_tokens(
        f"{subject}\n{body}\n{json.dumps(parsed, default=str)}"
    )
    matched_load_id = classification.get("matched_load_id")
    confidence = int(classification.get("confidence_score") or record.get("confidence_score") or 0)

    # ------------------------------------------------------------
    # Resolve conversation keys once, early.
    # Do not remove selected_conversation_key. The page needs it for
    # metadata, history, pending draft, replies, and load communications.
    # ------------------------------------------------------------
    conversation_key = (
        ops._safe_str(classification.get("conversation_key", ""))
        or ops._safe_str(record.get("conversation_key", ""))
        or f"intake-{selected_id}"
    )

    business_conversation_key = _ops_business_conversation_key(
        record=record,
        parsed=parsed,
        tokens=tokens,
        fallback_key=conversation_key,
    )

    selected_conversation_key = (
        ops._safe_str(business_conversation_key)
        or ops._safe_str(conversation_key)
        or ops._safe_str(ops._row_conversation_join_key(record))
        or f"intake-{selected_id}"
    )

    conversation_key = selected_conversation_key
    classification["conversation_key"] = selected_conversation_key

    current_record_conversation_key = ops._safe_str(record.get("conversation_key", ""))

    if selected_conversation_key and selected_conversation_key != current_record_conversation_key:
        try:
            ops._execute(
                """
                update order_intake
                set conversation_key = :conversation_key
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "conversation_key": selected_conversation_key,
                },
            )
            record["conversation_key"] = selected_conversation_key
        except Exception:
            pass

    # If this reply contains a direct update, merge that update into the pending draft.
    if pending_reply_updated_fields and selected_conversation_key:
        try:
            draft_update_sets = []
            draft_update_params = {
                "conversation_key": selected_conversation_key,
                "intake_id": int(selected_id),
                "latest_subject": subject,
                "latest_sender": sender,
                "parsed_data": json.dumps(parsed, default=str),
            }

            if pending_reply_updated_fields.get("Delivery Need Date"):
                draft_update_sets.append("delivery_need_date = :delivery_need_date")
                draft_update_params["delivery_need_date"] = pending_reply_updated_fields.get("Delivery Need Date")

            if pending_reply_updated_fields.get("Address"):
                draft_update_sets.append("delivery_address = :delivery_address")
                draft_update_params["delivery_address"] = pending_reply_updated_fields.get("Address")

            if draft_update_sets:
                ops._execute(
                    f"""
                    update public.order_intake_drafts
                    set {", ".join(draft_update_sets)},
                        request_stage = 'Pending Order Reply',
                        draft_status = case
                            when draft_status = 'Created' then draft_status
                            else 'Pending Review'
                        end,
                        latest_direction = 'inbound',
                        latest_subject = :latest_subject,
                        latest_sender = :latest_sender,
                        last_customer_message_at = now(),
                        last_intake_id = :intake_id,
                        parsed_data = coalesce(parsed_data, '{{}}'::jsonb) || cast(:parsed_data as jsonb)
                    where created_load_id is null
                    and (
                            conversation_key = :conversation_key
                        or booking_number = :conversation_key
                        or container_number = :conversation_key
                    )
                    """,
                    draft_update_params,
                )
        except Exception:
            pass

    pending_draft_merge_fields = _ops_pending_draft_fields_from_parsed(parsed)

    if pending_draft_merge_fields and selected_conversation_key and matched_load_id is None:
        try:
            ops._update_pending_order_draft_fields(
                conversation_key=selected_conversation_key,
                fields=pending_draft_merge_fields,
                last_intake_id=int(selected_id),
                latest_subject=subject,
                latest_sender=sender,
                request_stage=(
                    "Pending Order Reply"
                    if subject.lower().startswith(("re:", "fw:", "fwd:"))
                    else "Initial Order Request"
                ),
            )
        except Exception:
            pass
    # If the email is clearly a new booking, persist that correction so the top table moves it to New Orders.
    is_true_new_booking = (
        _ops_has_true_new_order_signal(subject, body, record)
        and not _ops_has_reply_or_update_signal(subject, body, record)
    )

    if is_true_new_booking:
        detected_type = "New Booking"
        record["request_type"] = "New Booking"
        record["request_type_clean"] = "New Booking"
        record["work_queue"] = "New Orders"
        record["dispatcher_queue"] = "New Orders"

        current_saved_type = ops._safe_str(record.get("request_type", ""))
        try:
            ops._execute(
                """
                update order_intake
                set request_type = 'New Booking',
                    conversation_key = :conversation_key,
                    action_required = coalesce(nullif(action_required, ''), 'Review order details.'),
                    confidence_score = greatest(coalesce(confidence_score, 0), 95)
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "conversation_key": conversation_key,
                },
            )
        except Exception:
            pass


    saved_matched_load_id = record.get("matched_load_id")
    if matched_load_id is None and pd.notna(saved_matched_load_id) and ops._safe_str(saved_matched_load_id):
        try:
            matched_load_id = int(saved_matched_load_id)
            classification["matched_load_id"] = matched_load_id
        except Exception:
            pass    

    final_decision = _build_dispatcher_decision(
        selected_id=int(selected_id),
        record=record,
        parsed=parsed,
        tokens=tokens,
        detected_type=detected_type,
        subject=subject,
        body=body,
        sender=sender,
        matched_load_id=matched_load_id,
        confidence=int(confidence or 0),
        conversation_key=conversation_key,
    )

    # Make the rest of the page obey the same final dispatcher decision.
    record["dispatcher_queue"] = final_decision["queue"]
    record["work_queue"] = final_decision["queue"]
    record["recommended_action"] = final_decision["recommended_action"]

    pending_draft_result = None

    should_track_pending_order_draft = (
        not matched_load_id
        and conversation_key
        and (
            final_decision.get("queue") == "New Orders"
            or final_decision.get("work_type") in {"New Booking", "Possible New Booking", "Pending Order Reply"}
            or "new booking" in ops._safe_str(subject).lower()
        )
    )

    if should_track_pending_order_draft:
        with timing.stage("pending_draft_upsert"):
            pending_draft_result = _ops_upsert_pending_order_draft(
                selected_id=int(selected_id),
                record=record,
                parsed=parsed,
                tokens=tokens,
                subject=subject,
                sender=sender,
                body=body,
                conversation_key=conversation_key,
                final_decision=final_decision,
                direction=ops._safe_str(record.get("email_direction") or "inbound") or "inbound",
            )

        if pending_draft_result.get("saved"):
            record["draft_status"] = pending_draft_result.get("draft_status", "")
            record["request_stage"] = pending_draft_result.get("request_stage", "")

            if pending_draft_result.get("request_stage") == "Pending Order Reply":
                try:
                    ops._execute(
                        """
                        update order_intake
                        set request_type = 'Pending Order Reply',
                            review_status = 'Open',
                            action_required = 'Customer replied to pending order draft. Review updated order details.',
                            conversation_key = :conversation_key
                        where id = :intake_id
                        """,
                        {
                            "intake_id": int(selected_id),
                            "conversation_key": conversation_key,
                        },
                    )
                except Exception:
                    pass
        else:
            st.caption(f"Pending draft save skipped: {pending_draft_result.get('error', '')}")

    current_attachments = ops._extract_operations_attachments(parsed, record)
    attachment_fields: dict = {}
    for attachment in current_attachments:
        attachment_parsed = ops._coerce_json_dict(attachment.get("parsed_data"))
        for field, value in attachment_parsed.items():
            if value not in ("", None, []) and not attachment_fields.get(field):
                attachment_fields[field] = value
    pending_draft = (
        _load_pending_order_draft_projection(conversation_key)
        if should_track_pending_order_draft
        else {}
    )
    final_decision["show_order_draft_review"] = should_show_order_draft_review(
        record,
        parsed,
        attachments=current_attachments,
        pending_draft=pending_draft,
        body=body,
        final_decision=final_decision,
    )
    final_decision["order_draft_projection"] = build_order_draft_projection(
        record=record,
        parsed_data=parsed,
        pending_draft=pending_draft,
        attachment_fields=attachment_fields,
        email_fields=parsed.get("_email_parsed"),
        tokens=tokens,
    )
    with timing.stage("order_draft_and_attachment_metadata_render"):
        _render_new_order_review_panel(
            selected_id=int(selected_id),
            record=record,
            parsed=parsed,
            tokens=tokens,
            subject=subject,
            sender=sender,
            body=body,
            conversation_key=conversation_key,
            final_decision=final_decision,
            matched_load_id=matched_load_id,
            timing=timing,
        )
    record["llm_review_required"] = bool(final_decision["llm_required"])
    record["llm_review_reason"] = final_decision.get("llm_reason", "")
    
    can_open_operations_case = bool(final_decision.get("allow_open_case")) and ops._operations_can_open_case(record, detected_type)
    with timing.stage("operations_case_loading"):
        operations_case = (
            ops._load_operations_case_by_id(record.get("case_id"))
            if can_open_operations_case
            else {}
        )
    if not can_open_operations_case:
        for record_field in [
            "case_id",
            "case_number",
            "case_status",
            "case_owner",
            "case_priority",
            "case_linked_load_id",
            "case_next_action",
            "case_sla_status",
            "case_first_response_due_at",
            "case_resolution_due_at",
        ]:
            record[record_field] = None
    if operations_case:
        for case_field, record_field in [
            ("id", "case_id"),
            ("case_number", "case_number"),
            ("status", "case_status"),
            ("owner", "case_owner"),
            ("priority", "case_priority"),
            ("linked_load_id", "case_linked_load_id"),
            ("next_action", "case_next_action"),
            ("sla_status", "case_sla_status"),
            ("first_response_due_at", "case_first_response_due_at"),
            ("resolution_due_at", "case_resolution_due_at"),
        ]:
            record[record_field] = operations_case.get(case_field)
        case_load_id = ops._int_or_none(operations_case.get("linked_load_id"))
        if matched_load_id is None and case_load_id is not None:
            matched_load_id = case_load_id
            classification["matched_load_id"] = matched_load_id

    load_context_key = f"operations_load_context_{selected_id}_{matched_load_id or 'none'}"
    cached_load_context = st.session_state.get(load_context_key) or {}
    load_context = cached_load_context.get("load_context", {})
    load_candidates = cached_load_context.get("load_candidates", [])

    selected_service_flow = extract_service_flow_value(record) or extract_service_flow_value({"parsed_data": parsed}) or "-"

    summary_customer = (
        ops._safe_str(record.get("case_customer", ""))
        or ops._safe_str(parsed.get("Customer", ""))
        or ops._safe_str(sender).split("<")[0].strip()
        or "-"
    )
    summary_bits = [
        f"Customer: {summary_customer}",
        f"Queue: {ops._safe_str(record.get('dispatcher_queue', '')) or '-'}",
        f"Booking: {ops._safe_str(parsed.get('Booking Number', '')) or tokens.get('booking_number', '') or '-'}",
        f"Container: {ops._safe_str(parsed.get('Container Number', '')) or tokens.get('container_number', '') or '-'}",
    ]
    recommended_next_action = final_decision["recommended_action"]

    if classification.get("learning_applied"):
        st.caption(classification.get("learning_reason", "Routing used recent dispatcher learning."))

    if can_open_operations_case or operations_case:
        with st.expander("Operations Case (exceptions / follow-up)", expanded=bool(operations_case)):
            if operations_case:
                ops._render_operations_case_summary_header(
                    operations_case=operations_case,
                    record=record,
                    parsed=parsed,
                    tokens=tokens,
                    matched_load_id=matched_load_id,
                )
            else:
                st.caption("Open a case only for exceptions, escalations, or follow-up that needs case tracking.")
                if st.button("Open Case", key=f"open_operations_case_{selected_id}", use_container_width=True):
                    created_case = ops._sync_operations_case_for_intake_record(record)
                    created_case_id = ops._int_or_none(created_case.get("id"))
                    if created_case_id is not None:
                        ops._log_operations_case_event(
                            created_case_id,
                            "opened",
                            "Operations Case opened",
                            f"Dispatcher opened a case from Operations request #{selected_id}.",
                            actor="dispatcher",
                        )
                    ops.refresh_data()
                    st.success("Operations Case opened for this order email.")
                    st.rerun()
    
    selected_booking = (
        ops._safe_str(tokens.get("booking_number"))
        or ops._safe_str(parsed.get("Booking Number"))
        or ops._safe_str(record.get("booking_hint", ""))
    )

    selected_container = (
        ops._safe_str(tokens.get("container_number"))
        or ops._safe_str(parsed.get("Container Number"))
        or ops._safe_str(record.get("container_hint", ""))
    )

    selected_reference = (
        ops._safe_str(tokens.get("reference_number"))
        or ops._safe_str(parsed.get("Reference Number"))
        or ops._safe_str(record.get("reference_hint", ""))
    )

    history_expander = st.expander("Communication History", on_change="rerun")
    if history_expander.open:
        with timing.stage("communication_history_loading"):
            history_df = ops._load_operations_business_conversation_timeline(
                conversation_key=selected_conversation_key,
                booking_number=selected_booking,
                container_number=selected_container,
                reference_number=selected_reference,
                limit=50,
            )

        with history_expander:
            if history_df is None or history_df.empty:
                st.info("No additional messages found for this booking/conversation yet.")
            else:
                history_display = history_df.copy()

                history_display["Time"] = pd.to_datetime(
                    history_display["source_received_at"].fillna(history_display["created_at"]),
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")

                history_display["Direction"] = history_display["email_direction"].fillna("inbound").astype(str)
                history_display["Thread Status"] = history_display["conversation_status"].fillna("").astype(str)
                history_display["Request Status"] = history_display["review_status"].fillna("").astype(str)
                history_display["From"] = history_display["source_sender"].fillna("").astype(str)
                history_display["Subject"] = history_display["source_subject"].fillna("").astype(str)

                if "message_preview" in history_display.columns:
                    history_display["Preview"] = history_display["message_preview"].fillna("").astype(str)
                else:
                    history_display["Preview"] = history_display["raw_text"].fillna("").astype(str).str.slice(0, 160)

                latest_direction = ops._safe_str(history_display.iloc[-1].get("Direction", "inbound")).title()
                latest_status = ops._safe_str(history_display.iloc[-1].get("Thread Status", ""))

                h1, h2, h3 = st.columns(3)
                h1.metric("Messages", len(history_display))
                h2.metric("Latest Direction", latest_direction or "-")
                h3.metric("Thread Status", latest_status or "-")

                st.caption(
                    "Showing strict conversation history by booking/container/reference. "
                    "Broad unrelated inbox messages are excluded."
                )

                display_cols = [
                    "Time",
                    "Direction",
                    "Thread Status",
                    "Request Status",
                    "From",
                    "Subject",
                    "Preview",
                ]

                history_source_df = history_df.reset_index(drop=True).copy()
                history_table_df = history_display[display_cols].reset_index(drop=True).copy()

                history_event = st.dataframe(
                    history_table_df,
                    use_container_width=True,
                    hide_index=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    key=f"operations_history_table_{selected_id}_{selected_conversation_key}",
                )

                selected_history_rows = history_event.selection.rows

                if selected_history_rows:
                    selected_history_index = selected_history_rows[0]
                    selected_history_row = history_source_df.iloc[selected_history_index].to_dict()

                    try:
                        selected_history_id = int(selected_history_row.get("id"))
                    except Exception:
                        selected_history_id = None

                    if selected_history_id is not None:
                        selected_history_detail_df = ops._load_operations_intake_message(selected_history_id)

                        if selected_history_detail_df is not None and not selected_history_detail_df.empty:
                            selected_history_detail = selected_history_detail_df.iloc[0].to_dict()
                        else:
                            selected_history_detail = selected_history_row

                        with st.container(border=True):
                            st.markdown("#### Selected History Message")

                            h_col1, h_col2, h_col3 = st.columns([1.2, 1.2, 1.6])

                            h_col1.metric(
                                "Direction",
                                ops._safe_str(selected_history_detail.get("email_direction", "inbound")).title() or "-",
                            )

                            h_col2.metric(
                                "Status",
                                ops._safe_str(selected_history_detail.get("review_status", "-")) or "-",
                            )

                            h_col3.metric(
                                "Conversation",
                                ops._safe_str(selected_history_detail.get("conversation_key", "-")) or "-",
                            )

                            st.write(
                                f"**From:** {ops._safe_str(selected_history_detail.get('source_sender', ''))}"
                            )

                            st.write(
                                f"**Subject:** {ops._safe_str(selected_history_detail.get('source_subject', ''))}"
                            )

                            st.caption(
                                f"Received: {ops._safe_str(selected_history_detail.get('source_received_at', ''))} "
                                f"| Message ID: {ops._safe_str(selected_history_detail.get('source_message_id', ''))}"
                            )

                            st.text_area(
                                "Historical Message Body",
                                value=ops._safe_str(selected_history_detail.get("raw_text", "")),
                                height=280,
                                disabled=True,
                                key=f"operations_history_message_body_{selected_id}_{selected_history_id}",
                            )

                            parsed_history = ops._coerce_json_dict(selected_history_detail.get("parsed_data"))

                            if parsed_history:
                                with st.expander("Historical Parsed Fields", expanded=False):
                                    st.json(parsed_history)
            # -----------------------------------------------------------------

    if operations_case:
        _render_operations_case_panel(
            selected_id=int(selected_id),
            operations_case=operations_case,
            matched_load_id=matched_load_id,
            show_timeline=False,
        )

    saved_request_type = ops._safe_str(record.get("request_type", "")).strip()
    effective_saved_request_type = _effective_operations_request_type_for_row(record)

    if (
        effective_saved_request_type != saved_request_type
        and effective_saved_request_type in REQUEST_TYPES
    ):
        saved_request_type = effective_saved_request_type

    default_request_type = saved_request_type if saved_request_type in REQUEST_TYPES else detected_type

    # Request type is now treated as the current routing classification.
    # Manual correction is moved into Learning / Routing Notes below the email body.
    request_type = default_request_type

    detected_reply_language = _detect_customer_language(subject, body)

    reply_language = "Auto"
    reply_tone = "Professional"
    resolved_reply_language = _resolve_reply_language(reply_language, subject, body)

    selected_action_required = _action_required_for_request(
        request_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=matched_load_id,
    )

    request_type_label = (
        ops._safe_str(request_type)
        or ops._safe_str(default_request_type)
        or ops._safe_str(detected_type)
        or "Needs Classification"
    )

    with timing.stage("pending_draft_panel"):
        _render_active_pending_order_draft_panel(
            selected_id=int(selected_id),
            selected_conversation_key=selected_conversation_key,
            matched_load_id=matched_load_id,
            final_decision=final_decision,
            subject=subject,
            sender=sender,
        )
    # -----------------------------------------------------------------
    # Primary Email Action Center
    # -----------------------------------------------------------------
    st.markdown("### Primary Email Action Center")

    ai_suggestion_key = f"operations_ai_suggestion_{selected_id}"
    ai_version_key = f"operations_ai_suggestion_version_{selected_id}"
    ai_suggestion = st.session_state.get(ai_suggestion_key)

    selected_decision_action = st.session_state.get(
        f"operations_selected_primary_action_{selected_id}",
        "",
    )
    detected_reply_language_label = (
        ops._safe_str(locals().get("detected_reply_language", ""))
        or _detect_customer_language(subject, body)
        or "English"
    )
    send_reply = False

    with st.container(border=True):
        a1, a2, a3 = st.columns([1.2, 1.2, 1.6])

        a1.metric("Email Workflow", "Reply / Forward")
        a2.metric("Work Type", request_type_label)
        a3.metric("Detected Language", detected_reply_language_label)

        st.caption(
            "Use this panel to reply, reply-all, or forward the selected email. "
            "Routing corrections and learning notes are saved below the original email body."
        )

        # Use column object calls instead of `with` blocks so the email form
        # cannot accidentally remain indented inside the right column.
        settings_col1, settings_col2 = st.columns(2)

        reply_language = settings_col1.selectbox(
            "Reply Language",
            REPLY_LANGUAGE_OPTIONS,
            index=REPLY_LANGUAGE_OPTIONS.index("Auto") if "Auto" in REPLY_LANGUAGE_OPTIONS else 0,
            help=f"Auto detected: {detected_reply_language_label}. Choose Spanish or Bilingual when needed.",
            key=f"operations_reply_language_{selected_id}",
        )

        reply_tone = settings_col2.selectbox(
            "Reply Tone",
            REPLY_TONE_OPTIONS,
            index=REPLY_TONE_OPTIONS.index("Professional") if "Professional" in REPLY_TONE_OPTIONS else 0,
            key=f"operations_reply_tone_{selected_id}",
        )

        resolved_reply_language = _resolve_reply_language(reply_language, subject, body)
        st.caption(f"Reply draft language: {resolved_reply_language}; tone: {reply_tone}")

        ai_reply_body = ""
        if ai_suggestion and ai_suggestion.get("success"):
            ai_reply_language = ops._safe_str(ai_suggestion.get("response_language", ""))
            if not ai_reply_language or ai_reply_language == resolved_reply_language:
                ai_reply_body = ops._safe_str(ai_suggestion.get("reply_body", ""))

        reply_body_default = ai_reply_body or ops._default_operations_reply_body(
            request_type,
            parsed,
            matched_load_id,
            subject,
            body,
            reply_language=reply_language,
            reply_tone=reply_tone,
        )

        reply_key_seed = f"{request_type}_{resolved_reply_language}_{st.session_state.get(ai_version_key, 'rule')}"
        reply_key_suffix = re.sub(r"[^a-z0-9]+", "_", reply_key_seed.lower()).strip("_")

        reply_sender_options = ops._operations_reply_sender_options()
        suggested_reply_from = ops._suggested_operations_reply_sender(request_type, operations_case)

        if suggested_reply_from not in reply_sender_options:
            reply_sender_options.insert(0, suggested_reply_from)

        default_reply_from_index = (
            reply_sender_options.index(suggested_reply_from)
            if suggested_reply_from in reply_sender_options
            else 0
        )

        action_options = ["Reply to Customer", "Reply All", "Forward"]

        action_mode = st.selectbox(
            "Email Action",
            action_options,
            index=0,
            key=f"operations_action_mode_{selected_id}",
        )

        reply_from = st.selectbox(
            "Reply From",
            reply_sender_options,
            index=default_reply_from_index,
            key=f"operations_reply_from_{selected_id}",
        )

        action_key_suffix = re.sub(r"[^a-z0-9]+", "_", action_mode.lower()).strip("_")
        reply_from_key_suffix = re.sub(r"[^a-z0-9]+", "_", reply_from.lower()).strip("_")

        default_reply_to = "" if action_mode == "Forward" else _extract_email_address(sender)

        reply_cc_default = (
            ops._reply_all_cc_from_record(parsed, reply_from, default_reply_to)
            if action_mode == "Reply All"
            else ""
        )
        # ------------------------------------------------------------
        # Email fields and reply draft controls
        # Do not use st.form here because this panel has multiple buttons.
        # ------------------------------------------------------------

        reply_to = st.text_input(
            "To",
            value=default_reply_to,
            key=f"operations_reply_to_{selected_id}_{action_key_suffix}",
        )

        reply_cc = st.text_input(
            "CC",
            value=reply_cc_default,
            key=f"operations_reply_cc_{selected_id}_{action_key_suffix}_{reply_from_key_suffix}",
        )

        reply_subject = st.text_input(
            "Subject",
            value=ops._default_operations_action_subject(subject, request_type, action_mode),
            key=f"operations_reply_subject_{selected_id}_{action_key_suffix}",
        )

        default_reply_body = ops._safe_str(reply_body_default)

        pending_update_summary = ""
        pending_update_booking = (
            ops._safe_str(tokens.get("booking_number"))
            or ops._safe_str(parsed.get("Booking Number"))
            or ops._safe_str(record.get("booking_hint", ""))
        )

        try:
            pending_update_summary = pending_reply_update.get("summary", "")
        except Exception:
            pending_update_summary = ""

        is_reply_context = ops._safe_str(subject).lower().startswith(("re:", "fw:", "fwd:"))

        if is_reply_context and pending_update_summary:
            default_reply_body = _ops_pending_order_reply_body(
                booking=pending_update_booking,
                update_summary=pending_update_summary,
            )

        if not default_reply_body:
            default_reply_body = (
                "Hello,\n\n"
                "We received your email and our dispatch team is reviewing it now. "
                "We will follow up with the next update or any missing details.\n\n"
                "Thank you,\n"
                "CaliTrans Dispatch"
            )

        try:
            pending_update_summary = pending_reply_update.get("summary", "")
        except Exception:
            pending_update_summary = ""

        is_pending_order_reply_context = (
            final_decision.get("work_type") in {"Pending Order Reply", "New Booking", "Possible New Booking"}
            or ops._safe_str(subject).lower().startswith(("re:", "fw:", "fwd:"))
        )

        if is_pending_order_reply_context and pending_update_summary:
            default_reply_body = _ops_pending_order_reply_body(
                booking=pending_update_booking,
                update_summary=pending_update_summary,
            )

        if not default_reply_body:
            default_reply_body = (
                "Hello,\n\n"
                "We received your email and our dispatch team is reviewing it now. "
                "We will follow up with the next update or any missing details.\n\n"
                "Thank you,\n"
                "CaliTrans Dispatch"
            )

        reply_body_key = f"operations_reply_body_{selected_id}"

        if reply_body_key not in st.session_state:
            st.session_state[reply_body_key] = default_reply_body

        st.markdown("#### Draft Assistance")

        draft_col1, draft_col2, draft_col3 = st.columns(3)

        with draft_col1:
            generate_ai_reply = st.button(
                "Generate AI Reply Draft",
                key=f"generate_ai_reply_draft_{selected_id}",
                use_container_width=True,
            )

        with draft_col2:
            use_standard_template = st.button(
                "Use Standard Template",
                key=f"use_standard_reply_template_{selected_id}",
                use_container_width=True,
            )

        with draft_col3:
            clear_reply_draft = st.button(
                "Clear Draft",
                key=f"clear_reply_draft_{selected_id}",
                use_container_width=True,
            )

        if use_standard_template:
            st.session_state[reply_body_key] = default_reply_body
            st.rerun()

        if clear_reply_draft:
            st.session_state[reply_body_key] = ""
            st.rerun()

        if generate_ai_reply:
            if not is_operations_ai_configured():
                st.warning("Add OPENAI_API_KEY to enable AI reply drafts.")
            else:
                with st.spinner("Generating AI reply draft..."):
                    load_context = {}
                    load_candidates = []

                    should_include_load_context = (
                        ops._safe_str(final_decision.get("queue", "")).lower()
                        in {
                            "existing load updates",
                            "appointments / pin",
                            "documents",
                            "billing",
                        }
                        or bool(matched_load_id)
                    )

                    if should_include_load_context:
                        try:
                            load_context, load_candidates = ops._build_ai_load_context(classification, parsed)
                        except Exception:
                            load_context, load_candidates = {}, []

                    try:
                        feedback_examples = ops._recent_operations_ai_feedback_examples()
                    except Exception:
                        feedback_examples = []

                    ai_suggestion = ops.generate_operations_ai_suggestion(
                        subject=subject,
                        sender=sender,
                        body=body,
                        parsed=parsed,
                        rule_classification=ops._operations_ai_rule_context(
                            classification,
                            parsed,
                            subject,
                            body,
                        ),
                        load_context=load_context,
                        load_candidates=load_candidates,
                        feedback_examples=feedback_examples,
                        response_language=resolved_reply_language,
                        reply_tone=reply_tone,
                        company_name=ops._get_app_setting("COMPANY_NAME", "CaliTrans"),
                    )

                    st.session_state[ai_suggestion_key] = ai_suggestion
                    st.session_state[ai_version_key] = str(datetime.now().timestamp()).replace(".", "_")

                    if not ai_suggestion.get("success"):
                        st.warning(f"AI draft failed: {ai_suggestion.get('error', 'Unknown error')}")
                    else:
                        draft_reply = _ops_extract_ai_reply_draft(ai_suggestion)

                        if draft_reply:
                            st.session_state[reply_body_key] = draft_reply
                            st.success("AI reply draft added to the message box. Review before sending.")
                            st.rerun()
                        else:
                            st.warning("AI returned a suggestion, but no reply draft text was found.")

        reply_body = st.text_area(
            "Message",
            height=220,
            key=reply_body_key,
        )

        mark_waiting = st.checkbox(
            "Mark waiting on customer after sending",
            value=action_mode != "Forward",
            key=f"operations_reply_waiting_{selected_id}_{action_key_suffix}",
        )
        send_reply = False

        send_button_label = "Send / Record Email Action"

        if action_mode == "Forward":
            send_button_label = "Forward / Record Email Action"
        elif "Reply" in action_mode:
            send_button_label = "Send Reply / Record Action"

        st.caption("Ready to send or record this email action.")

        send_col, spacer_col = st.columns([1, 3])

        with send_col:
            send_reply = st.button(
                send_button_label,
                key=f"send_record_email_action_{selected_id}_{action_key_suffix}_{reply_from_key_suffix}",
                type="primary",
                use_container_width=True,
        )
        
        if send_reply:
            if not reply_to.strip():
                st.error("Reply recipient is required.")
            elif not reply_subject.strip() or not reply_body.strip():
                st.error("Subject and message are required.")
            else:
                current_case_id = ops._int_or_none(record.get("case_id")) or ops._int_or_none(
                    ops._get_operations_case_id(ops._get_operations_case(selected_id))
                )

                try:
                    ops._send_smtp_email(
                        reply_to.strip(),
                        reply_subject.strip(),
                        reply_body.strip(),
                        from_email=reply_from,
                        cc_email=reply_cc.strip(),
                    )

                    ops._save_operations_email_reply(
                        intake_id=int(selected_id),
                        load_id=matched_load_id,
                        case_id=current_case_id,
                        recipient=reply_to.strip(),
                        subject=reply_subject.strip(),
                        body=reply_body.strip(),
                        status="sent",
                    )

                    ops._insert_operations_thread_reply_record(
                        intake_id=int(selected_id),
                        record=record,
                        reply_from=reply_from,
                        reply_to=reply_to.strip(),
                        reply_cc=reply_cc.strip(),
                        reply_subject=reply_subject.strip(),
                        reply_body=reply_body.strip(),
                        request_type=request_type,
                        conversation_key=business_conversation_key or selected_conversation_key or conversation_key,
                        matched_load_id=matched_load_id,
                        case_id=current_case_id,
                    )

                    if ai_suggestion and ai_suggestion.get("success") and ai_reply_body:
                        normalized_ai_reply = " ".join(ai_reply_body.split())
                        normalized_final_reply = " ".join(reply_body.strip().split())

                        ops._save_operations_ai_feedback(
                            intake_id=int(selected_id),
                            load_id=matched_load_id,
                            source_subject=subject,
                            source_sender=sender,
                            ai_suggestion=ai_suggestion,
                            final_request_type=request_type,
                            final_action_required=selected_action_required,
                            final_reply_body=reply_body.strip(),
                            correction_type=(
                                "reply_edited"
                                if normalized_ai_reply != normalized_final_reply
                                else "reply_accepted"
                            ),
                            feedback_notes=ops._safe_str(
                                st.session_state.get(f"operations_ai_feedback_notes_{selected_id}", "")
                            ),
                        )

                    if matched_load_id is not None:
                        ops._save_load_communication(
                            matched_load_id,
                            int(selected_id),
                            business_conversation_key or selected_conversation_key or conversation_key,
                            request_type,
                            reply_subject.strip(),
                            reply_from,
                            reply_body.strip(),
                            direction="outbound",
                            case_id=current_case_id,
                        )

                    _complete_operations_email_action(
                        selected_id=int(selected_id),
                        mark_waiting=bool(mark_waiting),
                        reply_to=reply_to.strip(),
                        reply_subject=reply_subject.strip(),
                    )

                    if mark_waiting and current_case_id is not None:
                        ops._set_operations_case_status(
                            current_case_id,
                            "Waiting Customer",
                            "Reply sent; waiting on customer response.",
                        )

                    st.success(f"Email sent from {reply_from} to {reply_to.strip()}.")
                    refresh_data()
                    st.rerun()

                except Exception as exc:
                    try:
                        ops._save_operations_email_reply(
                            intake_id=int(selected_id),
                            load_id=matched_load_id,
                            case_id=current_case_id,
                            recipient=reply_to.strip(),
                            subject=reply_subject.strip(),
                            body=reply_body.strip(),
                            status="failed",
                            error_message=str(exc),
                        )
                    except Exception:
                        pass

                    st.error(f"Email was not sent: {exc}")
    # -----------------------------------------------------------------
    # Original email and routing learning
    # -----------------------------------------------------------------
    show_bottom_original_email = not bool(
        final_decision.get("show_order_draft_review")
    )
    if show_bottom_original_email:
        with st.expander("Original Email Body", expanded=True):
            st.write(f"**From:** {sender}")
            st.write(f"**Subject:** {subject}")
            st.text_area(
                "Message",
                value=body,
                height=220,
                disabled=True,
                key=f"operations_original_email_body_{selected_id}",
            )

    if (
        not final_decision.get("show_order_draft_review")
        and (
            final_decision.get("allow_attach_document")
            or final_decision.get("queue") == "Existing Load Updates"
        )
    ):
        _render_operations_attachment_preview(
            selected_id=int(selected_id),
            record=record,
            parsed=parsed,
            timing=timing,
        )

    with timing.stage("contextual_load_matching"):
        load_match_candidates = (
            classification.get("load_match_candidates")
            or (
                ops.find_load_match_candidates(
                    tokens,
                    parsed=parsed,
                    subject=subject,
                    body=body,
                    limit=5,
                )
                if final_decision.get("queue") == "Existing Load Updates"
                and matched_load_id is None
                else []
            )
        )
    if _should_show_contextual_load_match(
        final_decision=final_decision,
        matched_load_id=matched_load_id,
        load_match_candidates=load_match_candidates,
    ):
        with st.expander("Find / Match Existing Load", expanded=final_decision.get("recommended_action") == "Find / Match Existing Load"):
            if matched_load_id is not None:
                st.success(f"This request is linked to load {matched_load_id}.")
            if not load_match_candidates:
                st.info("No load match candidates found from booking, container, reference, customer/date, or vessel details.")
            else:
                st.dataframe(pd.DataFrame(load_match_candidates), use_container_width=True, hide_index=True)
                candidate_options = [None] + load_match_candidates

                def _load_match_label(option) -> str:
                    if option is None:
                        return "Select load candidate"
                    return (
                        f"Load {option.get('Load ID')} | {option.get('Match Score')}% | "
                        f"{option.get('Booking Number') or option.get('Container Number') or option.get('Reference Number') or '-'} | "
                        f"{option.get('Customer') or '-'}"
                    )

                selected_candidate = st.selectbox(
                    "Candidate Load",
                    candidate_options,
                    format_func=_load_match_label,
                    key=f"operations_load_match_candidate_{selected_id}",
                )
                c_accept, c_reject = st.columns(2)
                with c_accept:
                    if st.button(
                        "Accept Load Match",
                        key=f"operations_accept_load_match_{selected_id}",
                        use_container_width=True,
                        disabled=selected_candidate is None,
                    ):
                        accepted_load_id = int(selected_candidate["Load ID"])
                        accepted_key = _conversation_key_from_candidate(selected_candidate, conversation_key)
                        update_intake_classification(
                            int(selected_id),
                            request_type,
                            accepted_key,
                            accepted_load_id,
                            int(selected_candidate.get("Match Score", confidence) or confidence),
                            f"Dispatcher accepted load match {accepted_load_id}: {selected_candidate.get('Match Reason', '')}",
                        )
                        current_case_id = ops._int_or_none(record.get("case_id")) or ops._int_or_none(operations_case.get("id"))
                        if current_case_id is not None:
                            ops._update_operations_case(
                                case_id=current_case_id,
                                status="Attached to Load",
                                owner=ops._safe_str(operations_case.get("owner", "Dispatch")) or "Dispatch",
                                priority=ops._safe_str(operations_case.get("priority", "Normal")) or "Normal",
                                linked_load_id=accepted_load_id,
                                next_action=f"Load match accepted for load {accepted_load_id}.",
                            )
                        refresh_data()
                        st.success(f"Accepted load match {accepted_load_id}.")
                        st.rerun()
                with c_reject:
                    if st.button(
                        "Reject Suggested Match",
                        key=f"operations_reject_load_match_{selected_id}",
                        use_container_width=True,
                        disabled=matched_load_id is None and selected_candidate is None,
                    ):
                        execute(
                            """
                            update order_intake
                            set matched_load_id = null,
                                confidence_score = least(confidence_score, 60),
                                action_required = 'Dispatcher rejected suggested load match; review manually.'
                            where id = :intake_id
                            """,
                            {"intake_id": int(selected_id)},
                        )
                        current_case_id = ops._int_or_none(record.get("case_id")) or ops._int_or_none(operations_case.get("id"))
                        if current_case_id is not None:
                            ops._add_operations_case_note(current_case_id, "Dispatcher rejected suggested load match; manual review needed.")
                        refresh_data()
                        st.warning("Suggested load match rejected.")
                        st.rerun()

    ai_suggestion_key = f"operations_ai_suggestion_{selected_id}"
    ai_version_key = f"operations_ai_suggestion_version_{selected_id}"
    ai_suggestion = st.session_state.get(ai_suggestion_key)

    st.markdown("### Work Item Actions")
    st.caption("Use cases for exceptions, escalations, or follow-up that needs tracking.")

    current_case_id = (
        ops._int_or_none(record.get("case_id"))
        or ops._int_or_none(operations_case.get("id"))
    )
    message_text = f"{subject or ''} {body or ''}"
    order_identifier = (
        ops._safe_str(parsed.get("Booking Number", ""))
        or ops._safe_str(tokens.get("booking_number", ""))
        or ops._safe_str(parsed.get("Reference Number", ""))
        or ops._safe_str(tokens.get("reference_number", ""))
        or ops._safe_str(parsed.get("Container Number", ""))
        or ops._safe_str(tokens.get("container_number", ""))
    )
    order_customer = ops._safe_str(parsed.get("Customer", "")) or ops._safe_str(sender).split("<")[0].strip()
    order_evidence = _ops_order_evidence(parsed, tokens, subject, body, record)
    parsed_has_new_order_details = ops._has_new_order_details(message_text, parsed, tokens)
    can_create_order = False  # The reviewed-draft panel owns new-order creation.

    can_create_quote = request_type == "Quote Request" and ops._has_quote_details(message_text, parsed, tokens)
    fill_order_blanks = True
    if matched_load_id is not None:
        fill_order_blanks = st.checkbox(
            "When updating an existing order, fill blank order fields from parsed email/document data",
            value=True,
            key=f"operations_update_order_fill_blanks_{selected_id}",
        )

    action_record = record.copy()
    action_record["request_type_clean"] = request_type
    action_record["control_level"] = ops._operations_control_level_for_row(action_record)
    action_record["department_lane"] = ops._operations_department_lane_for_row(action_record)
    selected_control_level = ops._safe_str(action_record.get("control_level", ""))
    selected_department_lane = ops._safe_str(action_record.get("department_lane", ""))
    can_route_business = False  # Internal reassignment is admin-only.

    can_update_load_action = bool(final_decision.get("allow_update_load") and matched_load_id is not None)
    visible_action_count = sum(
        [
            bool(can_create_order),
            bool(can_update_load_action),
            bool(can_create_quote),
            bool(matched_load_id is not None and request_type == "Cancellation"),
            bool(can_route_business),
            True,
        ]
    )
    action_columns = st.columns(min(max(visible_action_count, 1), 4))
    action_index = 0

    def _next_action_column():
        nonlocal action_index
        column = action_columns[action_index % len(action_columns)]
        action_index += 1
        return column

    if can_create_order:
        with _next_action_column():
            if st.button("Create Order", use_container_width=True):
                booking = ops._safe_str(parsed.get("Booking Number", "")) or ops._safe_str(tokens.get("booking_number", "")) or order_identifier
                customer = order_customer
                parsed_notes = ops._safe_str(parsed.get("Dispatcher Notes", ""))
                creation_notes = f"Created from Operations Inbox request #{selected_id}"
                if parsed_notes:
                    creation_notes = f"{creation_notes}\n{parsed_notes}"

                if not order_identifier or not customer:
                    st.error("Customer plus booking, reference, or container is required.")
                else:
                    result = create_load_from_inbox_item(
                        int(selected_id),
                        {
                            "TYPE": parsed.get("TYPE", "Import") or "Import",
                            "Booking Number": booking,
                            "Reference Number": parsed.get("Reference Number") or tokens.get("reference_number"),
                            "Customer": customer,
                            "Container Number": parsed.get("Container Number") or tokens.get("container_number"),
                            "Port": parsed.get("Port", ""),
                            "Warehouse": parsed.get("Warehouse", "") or parsed.get("Address", ""),
                            "Address": parsed.get("Address", ""),
                            "Document Cutoff": parsed.get("Document Cutoff", ""),
                            "Delivery Need Date": parsed.get("Delivery Need Date", ""),
                            "LFD": parsed.get("LFD", ""),
                            "Size": parsed.get("Size", ""),
                            "Status": "New",
                            "Dispatcher Notes": creation_notes,
                        },
                        conversation_key=conversation_key,
                        request_type="New Booking",
                        subject=subject,
                        sender=sender,
                        body=body,
                        case_id=current_case_id,
                        case_priority=ops._safe_str(operations_case.get("priority", "Normal")) or "Normal",
                    )
                    load_id = result.get("load_id")

                    if load_id:
                        try:
                            ops._execute(
                                """
                                update public.order_intake_drafts
                                set draft_status = 'Created',
                                    request_stage = 'Order Created',
                                    created_load_id = :load_id,
                                    last_intake_id = :intake_id
                                where conversation_key = :conversation_key
                                """,
                                {
                                    "load_id": int(load_id),
                                    "intake_id": int(selected_id),
                                    "conversation_key": conversation_key,
                                },
                            )
                        except Exception:
                            pass

                    refresh_data()
                    st.success(f"Created order/load ID {load_id}.")
                    st.rerun()

    if can_update_load_action:
        with _next_action_column():
            if st.button("Update Load", use_container_width=True):
                update_result = update_load_from_inbox_item(
                    int(selected_id),
                    int(matched_load_id),
                    parsed,
                    fill_blank_only=fill_order_blanks,
                    conversation_key=conversation_key,
                    request_type=request_type,
                    subject=subject,
                    sender=sender,
                    body=body,
                    case_id=current_case_id,
                    case_owner=ops._safe_str(operations_case.get("owner", "Dispatch")) or "Dispatch",
                    case_priority=ops._safe_str(operations_case.get("priority", "Normal")) or "Normal",
                )

                ops._refresh_data()
                if update_result.get("error"):
                    st.error(update_result["error"])
                elif update_result.get("updated_fields"):
                    st.success("Request attached and updated fields: " + ", ".join(update_result["updated_fields"]))
                else:
                    st.success("Request attached to existing order communication history.")
                st.rerun()

    if can_create_quote:
        with _next_action_column():
            if st.button("Create Quote", use_container_width=True):
                create_quote_request_from_intake(int(selected_id), parsed, body[:1000])

                ops._execute(
                    """
                    update order_intake
                    set review_status = 'Quote Created',
                        request_type = 'Quote Request',
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "conversation_key": conversation_key,
                    },
                )
                if current_case_id is not None:
                    ops._set_operations_case_status(current_case_id, "In Review", "Quote request created; waiting for pricing follow-up.")
                    ops._add_operations_case_note(current_case_id, f"Quote request created from Operations Inbox request #{selected_id}.")

                ops._refresh_data()
                st.success("Quote request created.")
                st.rerun()

    if matched_load_id is not None and request_type == "Cancellation":
        with _next_action_column():
            if st.button("Cancel Order", use_container_width=True):
                ops._execute(
                    """
                    update loads
                    set status = 'Cancelled'
                    where id = :load_id
                    """,
                    {"load_id": matched_load_id},
                )

                ops._execute(
                    """
                    update order_intake
                    set review_status = 'Order Cancelled',
                        matched_load_id = :matched_load_id,
                        request_type = 'Cancellation',
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "matched_load_id": matched_load_id,
                        "conversation_key": conversation_key,
                    },
                )
                if current_case_id is not None:
                    ops._set_operations_case_status(current_case_id, "Closed", f"Load {matched_load_id} cancelled.")
                    ops._add_operations_case_note(current_case_id, f"Cancelled matched load {matched_load_id}.")

                ops._refresh_data()
                st.warning("Matched order was cancelled.")
                st.rerun()

    if can_route_business:
        with _next_action_column():
            route_button_label = "Send to Billing" if selected_department_lane == "Accounting" else "Route Business"
            if st.button(route_button_label, use_container_width=True):
                business_lane = selected_department_lane or "Management"
                business_request_type = "Billing" if business_lane == "Accounting" else "Business Communication"
                business_owner = {
                    "Accounting": "Billing",
                    "Management": "Manager",
                    "Sales": "Customer Service",
                    "Safety": "Safety",
                    "Customer Service": "Customer Service",
                }.get(business_lane, "Manager")
                business_note = f"Business communication routed to {business_lane}."

                ops._execute(
                    """
                    update order_intake
                    set review_status = 'Open',
                        request_type = :request_type,
                        action_required = :action_required,
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "request_type": business_request_type,
                        "action_required": business_note,
                        "conversation_key": conversation_key,
                    },
                )
                if current_case_id is not None:
                    ops._update_operations_case(
                        case_id=current_case_id,
                        status="Waiting Billing" if business_owner == "Billing" else "In Review",
                        owner=business_owner,
                        priority=ops._safe_str(operations_case.get("priority", "Normal")) or "Normal",
                        linked_load_id=matched_load_id,
                        next_action=business_note,
                    )
                    ops._add_operations_case_note(current_case_id, business_note)

                ops._refresh_data()
                st.success(business_note)
                st.rerun()

    with _next_action_column():
        close_label = "Close Spam" if request_type == "Spam/Marketing" else "Archive / No Action" if selected_control_level == "Level 3 - No Action / Archive" else "Close / No Action"
        if st.button(close_label, use_container_width=True):
            ops._execute(
                """
                update order_intake
                set review_status = 'Closed',
                    request_type = case
                        when :request_type = 'Spam/Marketing' then 'Spam/Marketing'
                        when :control_level = 'Level 3 - No Action / Archive' then 'No Action / FYI'
                        else request_type
                    end,
                    action_required = case
                        when :request_type = 'Spam/Marketing' then 'Closed as spam / non-operational email.'
                        when :control_level = 'Level 3 - No Action / Archive' then 'Archived as no-action / FYI email.'
                        else action_required
                    end
                where id = :intake_id
                """,
                {"intake_id": int(selected_id), "request_type": request_type, "control_level": selected_control_level},
            )
            if current_case_id is not None:
                close_note = (
                    "Closed as spam / non-operational email."
                    if request_type == "Spam/Marketing"
                    else "Archived as no-action / FYI email."
                    if selected_control_level == "Level 3 - No Action / Archive"
                    else "Closed from Operations Inbox with no further action."
                )
                ops._set_operations_case_status(current_case_id, "Closed", close_note)

            ops._refresh_data()
            st.info("Request closed.")
            close_work_item(st.session_state)
            st.rerun()

    if initial_open_timing:
        timing.finish(state=st.session_state, logger=logger)
        st.session_state.pop(WORK_ITEM_OPEN_STARTED_AT, None)
