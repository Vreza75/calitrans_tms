# services/operations_inbox_service.py

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import nullcontext
from datetime import datetime
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st
import services.operations_attachment_service as attachment_service

from db_client import column_exists, execute, read_df
from services.email_parser import (
    _append_note,
    detect_container_quantity_mismatch,
    detect_order_blocks,
    extract_latest_email_body,
    parse_email_text,
)
from services.order_intake import create_load_from_intake
from services.operations_field_service import derive_review_state, extract_operational_fields
import services.operations_case_service as case_service
from services.operations_email_triage_service import (
    has_actual_billing_request,
    is_booking_confirmation,
    triage_operations_email,
)

# Process-level "schema already confirmed ready" cache, keyed by a short
# name per ensure_*_schema() function (see ensure_operations_fast_triage_
# schema/ensure_operations_email_sync_schema). One Streamlit worker
# process serves many browser sessions; st.session_state alone made every
# new session pay a column_exists() round trip even right after another
# session in the same process had just confirmed the schema.
_SCHEMA_READY_FLAGS: set[str] = set()




extract_operations_attachments = attachment_service.extract_operations_attachments
extract_operations_pdf_attachments = attachment_service.extract_operations_pdf_attachments
group_operations_source_documents = attachment_service.group_operations_source_documents
merge_operations_order_fields = attachment_service.merge_operations_order_fields
merge_operations_body_parsed_fields = attachment_service.merge_operations_body_parsed_fields
parse_saved_operations_attachment = attachment_service.parse_saved_operations_attachment
parse_saved_operations_pdf = attachment_service.parse_saved_operations_pdf
read_operations_attachment_bytes = attachment_service.read_operations_attachment_bytes
read_operations_pdf_bytes = attachment_service.read_operations_pdf_bytes
rescan_operations_request_attachments = attachment_service.rescan_operations_request_attachments
save_operations_attachment = attachment_service.save_operations_attachment
save_operations_pdf_attachment = attachment_service.save_operations_pdf_attachment
store_operations_parsed_data = attachment_service.store_operations_parsed_data
update_load_from_operations_pdf = attachment_service.update_load_from_operations_pdf
attach_saved_operations_file_to_load = attachment_service.attach_saved_operations_file_to_load
attach_saved_pdf_to_load = attachment_service.attach_saved_pdf_to_load
import_uploaded_operations_attachment = attachment_service.import_uploaded_operations_attachment
field_count = attachment_service.field_count
is_pdf_filename = attachment_service.is_pdf_filename
merge_saved_attachment_fields = attachment_service.merge_saved_attachment_fields

_extract_operations_attachments = extract_operations_attachments
_extract_operations_pdf_attachments = extract_operations_pdf_attachments
_group_operations_source_documents = group_operations_source_documents
_merge_operations_order_fields = merge_operations_order_fields
_merge_operations_body_parsed_fields = merge_operations_body_parsed_fields
_parse_saved_operations_attachment = parse_saved_operations_attachment
_parse_saved_operations_pdf = parse_saved_operations_pdf
_read_operations_attachment_bytes = read_operations_attachment_bytes
_read_operations_pdf_bytes = read_operations_pdf_bytes
_rescan_operations_request_attachments = rescan_operations_request_attachments
_save_operations_attachment = save_operations_attachment
_save_operations_pdf_attachment = save_operations_pdf_attachment
_store_operations_parsed_data = store_operations_parsed_data
_update_load_from_operations_pdf = update_load_from_operations_pdf
_attach_saved_operations_file_to_load = attach_saved_operations_file_to_load
_attach_saved_pdf_to_load = attach_saved_pdf_to_load
_import_uploaded_operations_attachment = import_uploaded_operations_attachment
_field_count = field_count
_is_pdf_filename = is_pdf_filename
_merge_saved_attachment_fields = merge_saved_attachment_fields

from services.load_matching_service import (
    build_ai_load_context,
    existing_load_columns,
    fetch_ai_load_context,
    find_ai_load_candidates,
    find_load_match_candidates,
    find_matching_load,
    valid_ai_suggested_load_id,
)

try:
    from ui_components.ops_metric_card import render_ops_metric_card
except Exception:
    render_ops_metric_card = None

try:
    from ui_components.document_review_panel import render_document_review_panel
except Exception:
    render_document_review_panel = None

try:
    from ui_components.pdf_preview import render_pdf_preview
except Exception:
    render_pdf_preview = None

try:
    from services.operations_ai import (
        generate_operations_ai_suggestion,
        is_operations_ai_configured,
    )
except Exception:
    generate_operations_ai_suggestion = None

    def is_operations_ai_configured() -> bool:
        return False


# ============================================================
# Basic helpers
# ============================================================

def safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value_text = safe_str(value)
    if not value_text:
        return None

    try:
        return int(float(value_text))
    except Exception:
        return None


def coerce_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    return {}


def json_dump(data: dict) -> str:
    return json.dumps(data or {}, default=str)


def parse_date_or_none(value: Any):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


_CONTAINS_ANY_PATTERN_CACHE: dict[str, "re.Pattern"] = {}


def contains_any(text: str, terms: list[str]) -> bool:
    """Word-boundary substring check. Plain `term in lowered` previously let
    short terms like "exam" false-positive inside unrelated text such as the
    "example.com" placeholder domain used by every test fixture address."""
    lowered = str(text or "").lower()
    for term in terms:
        term_lower = term.lower()
        pattern = _CONTAINS_ANY_PATTERN_CACHE.get(term_lower)
        if pattern is None:
            pattern = re.compile(r"\b" + re.escape(term_lower) + r"\b")
            _CONTAINS_ANY_PATTERN_CACHE[term_lower] = pattern
        if pattern.search(lowered):
            return True
    return False
VALID_SERVICE_FLOWS = {
    "Import",
    "Export",
    "Local Import",
    "Local Export",
}


def enforce_authoritative_booking_triage(
    *,
    subject: str,
    body: str,
    parsed: dict | None,
    triage: dict | None,
    already_matched_load: bool = False,
) -> dict:
    """
    Prevent clear booking confirmations from being overwritten by broad
    Billing or Documents classifications.

    already_matched_load: when this message already matches a real existing
    load, it's an update to that load, never a new booking - even if it
    also carries strong booking-confirmation signals (booking number +
    container number). Skips the New Booking override in that case so an
    upstream Booking Update classification survives.
    """

    parsed = parsed if isinstance(parsed, dict) else {}
    corrected = dict(triage or {})

    if already_matched_load:
        return corrected

    booking_confirmation = is_booking_confirmation(
        subject,
        body,
        parsed,
    )

    actual_billing_request = has_actual_billing_request(
        subject,
        body,
    )

    if not booking_confirmation or actual_billing_request:
        return corrected

    booking_number = safe_str(
        parsed.get("Booking Number")
        or parsed.get("booking_number")
        or parsed.get("Booking")
    )

    corrected.update(
        {
            "request_type": "New Booking",
            "work_level": "Level 1 - Operational Cases",
            "department_lane": "Dispatch",
            "work_queue": "New Orders",
            "store_only": False,
            "status": "Complete",
            "engine": safe_str(corrected.get("engine")) or "authoritative_booking_guard_v1",
            "triage_reason": (
                "Booking confirmation evidence overrides passive billing, "
                "invoice, rate-sheet, and bill-of-lading language."
            ),
            "action_required": (
                "Review booking details and create the required container work orders."
            ),
            "llm_required": False,
            "llm_review_required": False,
        }
    )

    if booking_number:
        corrected["conversation_key"] = booking_number

    try:
        current_confidence = int(
            float(corrected.get("confidence_score", 0) or 0)
        )
    except Exception:
        current_confidence = 0

    corrected["confidence_score"] = max(current_confidence, 95)

    tags = corrected.get("tags")
    if not isinstance(tags, list):
        tags = []

    if "booking_confirmation" not in tags:
        tags.append("booking_confirmation")

    if "authoritative_booking_guard" not in tags:
        tags.append("authoritative_booking_guard")

    corrected["tags"] = tags

    return corrected


def enforce_container_quantity_mismatch_review(parsed: dict, triage: dict | None) -> dict:
    """Correction pass, same shape as enforce_authoritative_booking_triage:
    runs after triage and overrides specific fields when a declared
    container quantity disagrees with how many container numbers were
    actually found (see detect_container_quantity_mismatch).

    Gated to booking-shaped request types only - an unrelated message
    (a billing question, a POD request, a driver update) that happens to
    quote an old booking confirmation containing a quantity/container
    mismatch in its body must not have its real classification's routing
    silently overwritten."""
    corrected = dict(triage or {})
    if safe_str(corrected.get("request_type")) not in {"New Booking", "Booking Update", "Customer Request", "Missing Information", "Other", ""}:
        return corrected

    mismatch = detect_container_quantity_mismatch(parsed)
    if mismatch is None:
        return corrected

    corrected.update(
        {
            "llm_review_required": True,
            "work_queue": "Review",
            "action_required": _append_note(corrected.get("action_required", ""), mismatch["message"]),
            "triage_reason": _append_note(corrected.get("triage_reason", ""), mismatch["message"]),
        }
    )
    return corrected


def _assign_split_row_identity(records: list[dict], base_message_id: str) -> list[dict]:
    """Assigns row-identity fields (message_id, thread_id) when more than
    one record was produced (a detected multi-order split). A single-record
    result is returned completely untouched - order_intake's unique index
    on source_message_id is never an issue for today's single-order path,
    so nothing about it changes.

    Block 0 keeps the real base_message_id (source_message_id) so the
    single rerun-dedupe check in sync_operations_email_engine, which is
    keyed on that same base id, still finds it and skips the whole email
    on rerun. Block N>=1 gets a synthetic suffix to satisfy order_intake's
    unique index on source_message_id. Every record's thread_id is forced
    to base_message_id so a query for "all rows from this email" is
    email_thread_id = base_message_id. Pure - no DB/IO."""
    if len(records) <= 1:
        return records

    for index, record in enumerate(records):
        record["message_id"] = base_message_id if index == 0 else f"{base_message_id}::order-{index + 1}"
        record["thread_id"] = base_message_id
    return records


# Compatibility aliases used by the Streamlit page.
_is_booking_confirmation = is_booking_confirmation
_has_actual_billing_request = has_actual_billing_request
_enforce_authoritative_booking_triage = enforce_authoritative_booking_triage

def sql_literal_list(values: list[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def refresh_data() -> None:
    """Targeted cache invalidation for the Operations Inbox's own cached
    reads. Deliberately narrower than st.cache_data.clear(): that call
    wipes every @st.cache_data function across the whole app (Admin,
    Dispatch Board, Orders Management, Port Houston integration,
    Documents, order intake) even though only these four read paths ever
    go stale from an Operations Inbox action."""
    load_operations_inbox_df.clear()
    load_operations_inbox_record.clear()
    load_operations_conversation_summary_df.clear()
    load_operations_conversation_timeline.clear()


def render_ops_caption(text: str) -> None:
    st.caption(text)


# Temporary compatibility aliases
_safe_str = safe_str
_int_or_none = int_or_none
_coerce_json_dict = coerce_json_dict
_json_dump = json_dump
_parse_date_or_none = parse_date_or_none
_contains_any = contains_any
_sql_literal_list = sql_literal_list
_refresh_data = refresh_data
_execute = execute


# ============================================================
# Constants
# ============================================================

REQUEST_TYPES = [
    "New Booking",
    "Booking Update",
    "Appointment Update",
    "Quote Request",
    "Missing Information",
    "Cancellation",
    "Billing",
    "Business Communication",
    "Driver Issue",
    "Port Issue",
    "Customer Request",
    "POD Request",
    "No Action / FYI",
    "Spam/Marketing",
    "Other",
]

REPLY_LANGUAGE_OPTIONS = ["Auto", "English", "Spanish", "Bilingual"]
REPLY_TONE_OPTIONS = ["Professional", "Concise", "Friendly", "Apology / Delay"]

INBOX_TERMINAL_REVIEW_STATUSES = [
    "Order Created",
    "Attached",
    "Quote Created",
    "Order Cancelled",
    "Closed",
]

OPERATIONS_EMAIL_SYNC_SOURCES = [
    "operations_email",
    "operations_email_sent",
    "email_body",
    "email_combined",
]

DEFAULT_OPERATIONS_QUEUE_ORDER = [
    "Action Required",
    "New Orders",
    "Existing Loads",
    "Waiting",
    "Documents",
    "Billing",
    "Business",
    "Archive",
    "Review",
]

OPERATIONS_CONTROL_LEVELS = [
    "Level 1 - Operational Cases",
    "Level 2 - Business Communications",
    "Level 3 - No Action / Archive",
    "Needs Review",
]

OPERATIONS_CONTROL_LEVEL_DESCRIPTIONS = {
    "Level 1 - Operational Cases": "Shipment, load, appointment, driver, port, document, and customer work that should move through dispatch.",
    "Level 2 - Business Communications": "Important company communications for accounting, management, sales, safety, vendors, or administration.",
    "Level 3 - No Action / Archive": "FYI, marketing, newsletters, duplicate, spam, and other messages that should not become dispatcher work.",
    "Needs Review": "Low-confidence or unclear messages that need a person to choose Operational, Business, Archive, or Spam once.",
}

OPERATIONAL_REQUEST_TYPES = {
    "New Booking",
    "Booking Update",
    "Appointment Update",
    "Quote Request",
    "Missing Information",
    "Cancellation",
    "Driver Issue",
    "Port Issue",
    "Customer Request",
    "POD Request",
}

BUSINESS_REQUEST_TYPES = {"Billing", "Business Communication"}
NO_ACTION_REQUEST_TYPES = {"No Action / FYI", "Spam/Marketing"}

BUSINESS_COMMUNICATION_TERMS = [
    "insurance",
    "renewal",
    "contract",
    "agreement",
    "legal",
    "attorney",
    "claim",
    "bank",
    "loan",
    "utility",
    "vendor",
    "supplier",
    "equipment purchase",
    "software",
    "it support",
    "password",
    "recruiting",
    "resume",
    "candidate",
    "employment",
    "hr",
    "human resources",
    "new customer inquiry",
    "sales lead",
    "credit application",
]

NO_ACTION_COMMUNICATION_TERMS = [
    "advertisement",
    "newsletter",
    "unsubscribe",
    "promotion",
    "webinar",
    "container sales",
    "equipment marketing",
    "holiday notice",
    "office closed",
    "for your records only",
    "no action required",
    "do not reply",
]

VIP_OPERATIONS_DOMAINS = {
    "msc.com",
    "maersk.com",
    "hapag-lloyd.com",
    "evergreen-shipping.com",
    "porthouston.com",
}

OPERATIONS_ATTACHMENTS_KEY = "_operations_attachments"
OPERATIONS_PDF_ATTACHMENTS_KEY = "_operations_pdf_attachments"

OPERATIONS_ORDER_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Size",
    "Port",
    "Warehouse",
    "Address",
    "Full Return Terminal",
    "Delivery Need Date",
    "Document Cutoff",
    "LFD",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Contact Company",
    "Dispatcher Notes",
]

APPOINTMENT_INTENT_TERMS = [
    "have the container",
    "please have the container",
    "deliver at",
    "delivery time",
    "pickup time",
    "appointment",
    "appt",
    "scheduled",
    "confirmed time",
    "delivery appointment",
    "pickup appointment",
    "change appointment",
    "reschedule",
    "move appointment",
    "change time",
    "change date",
    "cita",
    "cita de entrega",
    "cita de recogida",
    "programar",
    "reprogramar",
    "cambiar hora",
    "cambiar fecha",
]

QUOTE_INTENT_TERMS = [
    "quote request",
    "rate request",
    "please quote",
    "need a quote",
    "need rate",
    "need pricing",
    "send rate",
    "pricing request",
    "price this load",
    "can you quote",
    "quote this",
    "rate this",
    "cotizacion",
    "cotización",
    "cotizar",
    "tarifa",
    "precio",
    "solicitud de tarifa",
    "pueden cotizar",
    "necesito tarifa",
]

# "Quote <origin> to/→/a <dest>" is a common dispatcher/customer shorthand
# that never phrases the request as one of the fixed QUOTE_INTENT_TERMS
# phrases above - the word "quote" itself immediately introduces a lane.
_QUOTE_LANE_SHORTHAND_RE = re.compile(r"\bquote\b\s+[A-Za-z][\w .,'-]{0,40}?\s(?:to|→|a)\s", re.I)

UPDATE_INTENT_TERMS = [
    "any update",
    "status update",
    "please update",
    "can you update",
    "where are we",
    "where is",
    "eta",
    "update",
    "revision",
    "revised",
    "changed",
    "new address",
    "correction",
    "container released",
    "released",
    "last free day",
    "lfd",
    "actualizacion",
    "actualización",
    "estado",
    "estatus",
    "alguna novedad",
    "donde esta",
    "dónde está",
    "contenedor liberado",
    "liberado",
    "ultimo dia libre",
    "último día libre",
]

INFORMATION_UPDATE_TERMS = [
    "please note",
    "note the",
    "fyi",
    "for your information",
    "for your records",
    "see below",
    "please see",
    "hours",
    "receiving hours",
    "delivery hours",
    "warehouse hours",
    "office hours",
    "correction",
    "corrected",
    "instead of",
    "revised",
    "updated",
    "confirmed",
    "pre-alert",
    "pre alert",
    "please be advised",
    "tomar nota",
    "para su informacion",
    "horario",
    "horarios",
    "correccion",
    "actualizado",
    "confirmado",
]

NEW_ORDER_INTENT_TERMS = [
    "new booking",
    "new load",
    "new import order",
    "new export order",
    "load order",
    "delivery order",
    "work order",
    "tender",
    "tendered",
    "bill of lading",
    "bol",
    "attached order",
    "attached load",
    "following shipment",
    "shipment details",
    "truckload details",
    "please book",
    "nuevo booking",
    "nueva carga",
    "orden de carga",
    "orden adjunta",
    "favor reservar",
]

ORDER_PLACEMENT_TERMS = [
    "new booking",
    "new load",
    "new import order",
    "new export order",
    "create order",
    "create load",
    "please book",
    "please arrange",
    "please schedule",
    "please dispatch",
    "please pick up",
    "please pickup",
    "please deliver",
    "need drayage",
    "following shipment",
    "shipment details",
    "truckload details",
    "set up this load",
    "setup this load",
    "load order attached",
    "delivery order attached",
    "attached delivery order",
    "attached load order",
    "nuevo booking",
    "nueva carga",
    "crear orden",
    "favor reservar",
    "favor programar",
    "favor recoger",
    "favor entregar",
]

MISSING_INFO_TERMS = [
    "missing info",
    "missing information",
    "need info",
    "need information",
    "incomplete",
    "please provide",
    "falta informacion",
    "falta información",
    "informacion faltante",
    "información faltante",
    "incompleto",
    "por favor envie",
    "por favor envíe",
]

CANCELLATION_TERMS = ["cancel", "cancelled", "canceled", "cancelar", "cancelado", "cancelacion", "cancelación"]
POD_TERMS = ["pod", "proof of delivery", "prueba de entrega", "comprobante de entrega"]
BILLING_TERMS = [
    "billing request",
    "billing question",
    "billing issue",
    "invoice request",
    "invoice correction",
    "invoice dispute",
    "invoice status",
    "incorrect invoice",
    "missing invoice",
    "payment request",
    "payment status",
    "payment due",
    "statement request",
    "accessorial invoice",
    "detention invoice",
    "demurrage invoice",
    "lumper receipt",
    "factura pendiente",
    "correccion de factura",
    "corrección de factura",
    "solicitud de factura",
    "problema de facturacion",
    "problema de facturación",]
DRIVER_ISSUE_TERMS = ["driver", "truck", "chassis", "flat tire", "breakdown", "accident", "late driver", "no show", "chofer", "conductor", "camion", "chasis", "accidente"]
PORT_ISSUE_TERMS = ["port hold", "terminal hold", "hold", "customs hold", "line hold", "exam", "x-ray", "gate issue", "trouble ticket", "puerto", "retenido", "aduana", "inspeccion"]
SPAM_MARKETING_TERMS = ["unsubscribe", "newsletter", "marketing", "promotion", "webinar", "seo", "lead generation", "limited time offer", "sales outreach"]

SPANISH_LANGUAGE_TERMS = [
    "actualizacion",
    "actualización",
    "carga",
    "contenedor",
    "cotizacion",
    "cotización",
    "entrega",
    "estado",
    "favor",
    "gracias",
    "hola",
    "informacion",
    "información",
    "necesito",
    "podria",
    "podría",
    "puede",
    "pueden",
    "recogida",
    "referencia",
    "reserva",
    "solicito",
]

ENGLISH_LANGUAGE_TERMS = [
    "appointment",
    "booking",
    "container",
    "delivery",
    "hello",
    "information",
    "please",
    "quote",
    "rate",
    "reference",
    "request",
    "status",
    "thank",
    "update",
]


# ============================================================
# Case-service wrappers
# ============================================================

def _case_attr(public_name: str, private_name: str | None = None):
    if hasattr(case_service, public_name):
        return getattr(case_service, public_name)
    if private_name and hasattr(case_service, private_name):
        return getattr(case_service, private_name)
    return None


def ensure_operations_case_schema() -> None:
    fn = _case_attr("ensure_operations_case_schema", "_ensure_operations_case_schema")
    if fn:
        fn()


def load_operations_case_by_id(case_id) -> dict:
    fn = _case_attr("load_operations_case_by_id", "_load_operations_case_by_id")
    if fn:
        return fn(case_id)

    case_id = int_or_none(case_id)
    if case_id is None:
        return {}

    try:
        case_df = read_df(
            """
            select *
            from operations_cases
            where id = :case_id
            limit 1
            """,
            {"case_id": case_id},
        )
    except Exception:
        return {}

    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def sync_operations_case_for_intake_record(record) -> dict:
    fn = _case_attr("sync_operations_case_for_intake_record", "_sync_operations_case_for_intake_record")
    if fn:
        return fn(record)
    return {}


def log_operations_case_event(*args, **kwargs) -> None:
    fn = _case_attr("log_operations_case_event", "_log_operations_case_event")
    if fn:
        fn(*args, **kwargs)


def add_operations_case_note(*args, **kwargs) -> None:
    fn = _case_attr("add_operations_case_note", "_add_operations_case_note")
    if fn:
        fn(*args, **kwargs)


def set_operations_case_status(*args, **kwargs) -> None:
    fn = _case_attr("set_operations_case_status", "_set_operations_case_status")
    if fn:
        fn(*args, **kwargs)


def update_operations_case(*args, **kwargs) -> None:
    fn = _case_attr("update_operations_case", "_update_operations_case")
    if fn:
        fn(*args, **kwargs)


def load_operations_case_timeline(case_id) -> pd.DataFrame:
    fn = _case_attr("load_operations_case_timeline", "_load_operations_case_timeline")
    if fn:
        return fn(case_id)
    return pd.DataFrame()


def operations_case_metrics() -> dict:
    fn = _case_attr("operations_case_metrics", "_operations_case_metrics")
    if fn:
        return fn()

    metrics = {"open": 0, "waiting_dispatch": 0, "waiting_customer": 0, "closed": 0}
    try:
        case_df = read_df(
            """
            select coalesce(status, 'New') as status, count(*) as case_count
            from operations_cases
            group by coalesce(status, 'New')
            """
        )
    except Exception:
        return metrics

    for _, row in case_df.iterrows():
        status = safe_str(row.get("status", "New"))
        count = int(row.get("case_count", 0) or 0)

        if status != "Closed":
            metrics["open"] += count
        if status == "Waiting Dispatcher":
            metrics["waiting_dispatch"] += count
        elif status == "Waiting Customer":
            metrics["waiting_customer"] += count
        elif status == "Closed":
            metrics["closed"] += count

    return metrics


# Compatibility aliases
_load_operations_case_by_id = load_operations_case_by_id
_sync_operations_case_for_intake_record = sync_operations_case_for_intake_record
_log_operations_case_event = log_operations_case_event
_add_operations_case_note = add_operations_case_note
_set_operations_case_status = set_operations_case_status
_update_operations_case = update_operations_case
_load_operations_case_timeline = load_operations_case_timeline
_operations_case_metrics = operations_case_metrics


# ============================================================
# Schema / email sync support
# ============================================================

def operations_email_source_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}source in ({sql_literal_list(OPERATIONS_EMAIL_SYNC_SOURCES)})"

def _is_strong_operations_business_key(value: str) -> bool:
    value = safe_str(value).strip()

    if not value:
        return False

    lowered = value.lower()

    if lowered in {
        "-",
        "none",
        "null",
        "nan",
        "unknown",
        "contact",
        "customer-request",
        "customer request",
    }:
        return False

    if lowered.startswith(("email-", "intake-", "intake:", "msg-", "message-", "cah+")):
        return False

    if "@" in lowered and "." in lowered:
        return False

    if not any(char.isdigit() for char in value):
        return False

    return len(value) >= 5
# Paste this into services/operations_inbox_service.py near the other conversation/timeline helpers.

def _is_strong_operations_business_key(value: str) -> bool:
    value = safe_str(value).strip()

    if not value:
        return False

    lowered = value.lower()

    if lowered in {
        "-",
        "none",
        "null",
        "nan",
        "unknown",
        "contact",
        "customer-request",
        "customer request",
    }:
        return False

    if lowered.startswith(("email-", "intake-", "intake:", "msg-", "message-", "cah+")):
        return False

    if "@" in lowered and "." in lowered:
        return False

    if not any(char.isdigit() for char in value):
        return False

    return len(value) >= 5


def load_operations_business_conversation_timeline(
    *,
    conversation_key: str = "",
    booking_number: str = "",
    container_number: str = "",
    reference_number: str = "",
    limit: int = 50,
) -> pd.DataFrame:
    """
    Strict booking/container/reference conversation timeline.

    This prevents broad unrelated histories such as 300+ unrelated messages.
    """

    keys: list[str] = []

    for value in [conversation_key, booking_number, container_number, reference_number]:
        clean = safe_str(value).strip()
        if _is_strong_operations_business_key(clean) and clean not in keys:
            keys.append(clean)

    if not keys:
        return pd.DataFrame()

    clauses = []
    params = {"limit": int(limit or 50)}

    for index, key in enumerate(keys):
        key_param = f"key_{index}"
        like_param = f"like_{index}"

        params[key_param] = key
        params[like_param] = f"%{key}%"

        clauses.append(
            f"""
            conversation_key = :{key_param}
            or source_subject ilike :{like_param}
            or raw_text ilike :{like_param}
            or parsed_data::text ilike :{like_param}
            """
        )

    where_sql = " or ".join(f"({clause})" for clause in clauses)

    try:
        return read_df(
            f"""
            select
                id,
                source_received_at,
                created_at,
                coalesce(email_direction, 'inbound') as email_direction,
                coalesce(conversation_status, 'New Conversation') as conversation_status,
                coalesce(review_status, 'Open') as review_status,
                coalesce(source_sender, '') as source_sender,
                coalesce(source_subject, '') as source_subject,
                coalesce(raw_text, '') as raw_text,
                left(coalesce(raw_text, ''), 180) as message_preview,
                coalesce(conversation_key, '') as conversation_key
            from order_intake
            where {operations_email_source_filter()}
              and ({where_sql})
            order by coalesce(source_received_at, created_at) asc, id asc
            limit :limit
            """,
            params,
        )
    except Exception:
        return pd.DataFrame()


_load_operations_business_conversation_timeline = load_operations_business_conversation_timeline
def load_operations_intake_message(intake_id: int) -> pd.DataFrame:
    """
    Load one full operations inbox message for historical review.
    Used when dispatcher clicks a Communication History row.
    """

    try:
        return read_df(
            """
            select
                id,
                source,
                source_subject,
                source_sender,
                source_received_at,
                source_message_id,
                email_direction,
                email_mailbox,
                email_thread_id,
                email_in_reply_to,
                email_references,
                conversation_key,
                conversation_status,
                review_status,
                request_type,
                action_required,
                confidence_score,
                matched_load_id,
                case_id,
                parsed_data,
                raw_text,
                created_at
            from order_intake
            where id = :intake_id
            limit 1
            """,
            {"intake_id": int(intake_id)},
        )
    except Exception:
        return pd.DataFrame()


_load_operations_intake_message = load_operations_intake_message


def _pending_order_required_missing_fields(fields: dict) -> list[str]:
    """
    Returns human-readable missing fields for pending order drafts.
    This is intentionally practical for dispatch review.
    """

    customer = safe_str(fields.get("customer", ""))
    service_flow = safe_str(fields.get("service_flow", ""))
    origin_port = safe_str(fields.get("origin_port", ""))
    destination_warehouse = safe_str(fields.get("destination_warehouse", ""))
    delivery_address = safe_str(fields.get("delivery_address", ""))
    booking_number = safe_str(fields.get("booking_number", ""))
    container_number = safe_str(fields.get("container_number", ""))

    missing = []

    if not customer:
        missing.append("Customer")

    if not service_flow:
        missing.append("Service Flow")

    if not origin_port:
        missing.append("Origin / Port")

    if not destination_warehouse and not delivery_address:
        missing.append("Destination / Warehouse / Address")

    if not booking_number and not container_number:
        missing.append("Booking or Container")

    return missing


def update_pending_order_draft_fields(
    *,
    conversation_key: str,
    fields: dict,
    last_intake_id: int | None = None,
    latest_subject: str = "",
    latest_sender: str = "",
    request_stage: str = "Pending Order Reply",
    dispatcher_confirmed: bool = False,
) -> bool:
    """
    Update the pending order draft with the latest agreed information.

    This does not create a load. It only keeps the pending draft current
    while dispatcher/client go back and forth.

    dispatcher_confirmed=True marks this call as an explicit dispatcher save
    (the "Save Latest Agreed Draft Details" form). That stamps
    dispatcher_confirmed_at, and every other caller (automated re-parses as
    new customer messages arrive) then skips overwriting fields on a draft
    that's already been confirmed — a weaker parser must never silently
    replace a value the dispatcher already signed off on.
    """

    conversation_key = safe_str(conversation_key).strip()
    if not conversation_key:
        return False

    fields = fields if isinstance(fields, dict) else {}

    allowed_fields =(
    {
        "customer": "customer",
        "booking_number": "booking_number",
        "container_number": "container_number",
        "container_size": "container_size",
        "service_flow": "service_flow",
        "origin_port": "origin_port",
        "destination_warehouse": "destination_warehouse",
        "delivery_address": "delivery_address",
        "delivery_need_date": "delivery_need_date",
        "lfd": "lfd",
        "reference_number": "reference_number",
        "container_qty": "container_qty",
        "commodity": "commodity",
        "empty_pickup_location": "empty_pickup_location",
        "full_return_terminal": "full_return_terminal",
        "port_of_loading": "port_of_loading",
        "port_of_discharge": "port_of_discharge",
        "place_of_delivery": "place_of_delivery",
        "exporting_carrier": "exporting_carrier",
        "vessel_name": "vessel_name",
        "steamship_line": "steamship_line",
        "opens_for_receiving": "opens_for_receiving",
        "document_cutoff": "document_cutoff",
        "vgm_cutoff": "vgm_cutoff",
        "cargo_cutoff": "cargo_cutoff",
        "sailing_date": "sailing_date",
        "eta_date": "eta_date",
        "rate_per_container": "rate_per_container",
        "rate_total": "rate_total",
        "dispatcher_notes": "dispatcher_notes",
    }
    )

    update_values = {}

    for field_name, column_name in allowed_fields.items():
        if column_name == "container_qty":
            qty = int_or_none(fields.get(field_name))
            if qty is not None and 1 <= qty <= 10:
                update_values[column_name] = qty
            continue
        value = safe_str(fields.get(field_name, "")).strip()
        if value:
            update_values[column_name] = value

    if not update_values:
        return False

    missing_fields = _pending_order_required_missing_fields(
        {
            **fields,
            **update_values,
        }
    )

    set_parts = []
    params = {
        "conversation_key": conversation_key,
        "last_intake_id": last_intake_id,
        "latest_subject": safe_str(latest_subject),
        "latest_sender": safe_str(latest_sender),
        "request_stage": safe_str(request_stage) or "Pending Order Reply",
        "missing_fields": json.dumps(missing_fields),
        "dispatcher_confirmed": bool(dispatcher_confirmed),
    }

    for column_name, value in update_values.items():
        if dispatcher_confirmed:
            set_parts.append(f"{column_name} = :{column_name}")
        else:
            # A draft the dispatcher already confirmed is frozen against
            # further automated overwrites of its fields; only an explicit
            # dispatcher save (above) can change it after that point.
            set_parts.append(
                f"{column_name} = case "
                f"when dispatcher_confirmed_at is null then :{column_name} "
                f"else {column_name} end"
            )
        params[column_name] = value

    set_sql = ",\n                    ".join(set_parts)

    sql_with_missing_fields = f"""
        update public.order_intake_drafts
        set
            {set_sql},
            request_stage = :request_stage,
            draft_status = case
                when created_load_id is not null then draft_status
                when :missing_fields = '[]' then 'Ready for Order Review'
                else 'Needs Details'
            end,
            dispatcher_confirmed_at = case
                when :dispatcher_confirmed then now()
                else dispatcher_confirmed_at
            end,
            latest_direction = 'inbound',
            latest_subject = :latest_subject,
            latest_sender = :latest_sender,
            last_customer_message_at = now(),
            last_intake_id = coalesce(:last_intake_id, last_intake_id),
            missing_fields = cast(:missing_fields as jsonb),
            updated_at = now()
        where created_load_id is null
          and (
                conversation_key = :conversation_key
             or booking_number = :conversation_key
             or container_number = :conversation_key
          )
    """

    sql_without_missing_fields = f"""
        update public.order_intake_drafts
        set
            {set_sql},
            request_stage = :request_stage,
            draft_status = case
                when created_load_id is not null then draft_status
                when :missing_fields = '[]' then 'Ready for Order Review'
                else 'Needs Details'
            end,
            dispatcher_confirmed_at = case
                when :dispatcher_confirmed then now()
                else dispatcher_confirmed_at
            end,
            latest_direction = 'inbound',
            latest_subject = :latest_subject,
            latest_sender = :latest_sender,
            last_customer_message_at = now(),
            last_intake_id = coalesce(:last_intake_id, last_intake_id),
            updated_at = now()
        where created_load_id is null
          and (
                conversation_key = :conversation_key
             or booking_number = :conversation_key
             or container_number = :conversation_key
          )
    """

    try:
        execute(sql_with_missing_fields, params)
        return True
    except Exception:
        try:
            execute(sql_without_missing_fields, params)
            return True
        except Exception:
            return False


_update_pending_order_draft_fields = update_pending_order_draft_fields
def load_operations_business_conversation_timeline(
        *,
        conversation_key: str = "",
        booking_number: str = "",
        container_number: str = "",
        reference_number: str = "",
        limit: int = 50,
    ) -> pd.DataFrame:
        """
        Strict booking/container/reference conversation timeline.

        This prevents broad unrelated histories such as 390-message timelines.
        """

        keys: list[str] = []

        for value in [conversation_key, booking_number, container_number, reference_number]:
            clean = safe_str(value).strip()
            if _is_strong_operations_business_key(clean) and clean not in keys:
                keys.append(clean)

        if not keys:
            return pd.DataFrame()

        clauses = []
        params = {"limit": int(limit or 50)}

        for index, key in enumerate(keys):
            key_param = f"key_{index}"
            like_param = f"like_{index}"

            params[key_param] = key
            params[like_param] = f"%{key}%"

            clauses.append(
                f"""
                conversation_key = :{key_param}
                or source_subject ilike :{like_param}
                or raw_text ilike :{like_param}
                or parsed_data::text ilike :{like_param}
                """
            )

        where_sql = " or ".join(f"({clause})" for clause in clauses)

        try:
            return read_df(
                f"""
                select
                    id,
                    source_received_at,
                    created_at,
                    coalesce(email_direction, 'inbound') as email_direction,
                    coalesce(conversation_status, 'New Conversation') as conversation_status,
                    coalesce(review_status, 'Open') as review_status,
                    coalesce(source_sender, '') as source_sender,
                    coalesce(source_subject, '') as source_subject,
                    coalesce(raw_text, '') as raw_text,
                    left(coalesce(raw_text, ''), 180) as message_preview,
                    coalesce(conversation_key, '') as conversation_key
                from order_intake
                where {operations_email_source_filter()}
                and ({where_sql})
                order by coalesce(source_received_at, created_at) asc, id asc
                limit :limit
                """,
                params,
            )
        except Exception:
            return pd.DataFrame()

def load_operations_business_conversation_timeline(
    *,
    conversation_key: str = "",
    booking_number: str = "",
    container_number: str = "",
    reference_number: str = "",
    limit: int = 50,
):
    """
    Strict business-conversation timeline.

    Avoid broad topic/customer matching. This prevents unrelated 300+ message
    timelines when the selected key is weak.
    """

    keys = []

    for value in [conversation_key, booking_number, container_number, reference_number]:
        clean_value = safe_str(value).strip()
        if _is_strong_operations_business_key(clean_value) and clean_value not in keys:
            keys.append(clean_value)

    if not keys:
        return read_df(
            """
            select
                id,
                source_received_at,
                created_at,
                email_direction,
                conversation_status,
                review_status,
                source_sender,
                source_subject,
                raw_text,
                parsed_data,
                ''::text as message_preview,
                conversation_key
            from order_intake
            where 1 = 0
            """
        )

    clauses = []
    params = {
        "limit": int(limit or 50),
    }

    for index, key in enumerate(keys):
        key_param = f"key_{index}"
        like_param = f"like_{index}"

        params[key_param] = key
        params[like_param] = f"%{key}%"

        clauses.append(
            f"""
            conversation_key = :{key_param}
            or source_subject ilike :{like_param}
            or raw_text ilike :{like_param}
            or parsed_data::text ilike :{like_param}
            """
        )

    where_sql = " or ".join(f"({clause})" for clause in clauses)

    return read_df(
        f"""
        select
            id,
            source_received_at,
            created_at,
            coalesce(email_direction, 'inbound') as email_direction,
            coalesce(conversation_status, 'New Conversation') as conversation_status,
            coalesce(review_status, 'Open') as review_status,
            coalesce(source_sender, '') as source_sender,
            coalesce(source_subject, '') as source_subject,
            coalesce(raw_text, '') as raw_text,
            parsed_data,
            left(coalesce(raw_text, ''), 180) as message_preview,
            coalesce(conversation_key, '') as conversation_key
        from order_intake
        where {where_sql}
        order by coalesce(source_received_at, created_at) asc, id asc
        limit :limit
        """,
        params,
    )


_load_operations_business_conversation_timeline = load_operations_business_conversation_timeline
def conversation_join_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    generic_conversation_key = (
        f"case when lower(coalesce({prefix}conversation_key, '')) in "
        "('contact', 'customer-request', 'customer request', 'unknown', 'none', 'null') "
        f"or lower(coalesce({prefix}conversation_key, '')) like 'email-%' "
        f"or lower(coalesce({prefix}conversation_key, '')) like 'intake-%' "
        f"then '' else {prefix}conversation_key end"
    )
    return (
        f"coalesce("
        f"nullif({prefix}email_thread_id, ''), "
        f"nullif({prefix}email_in_reply_to, ''), "
        f"nullif(({prefix}email_references ->> 0), ''), "
        f"nullif({prefix}source_message_id, ''), "
        f"nullif({generic_conversation_key}, ''), "
        f"nullif(lower(coalesce({prefix}source_sender, '') || '|' || "
        f"coalesce(nullif({prefix}email_normalized_subject, ''), {prefix}source_subject, '')), '|'), "
        f"concat('intake:', {prefix}id)"
        f")"
    )



def ensure_operations_fast_triage_schema() -> None:
    """Add optional fast-triage columns used by the Operations Inbox.

    The app still stores the full triage payload inside parsed_data under
    _fast_triage, but these columns make filtering and dashboards fast.

    Readiness is cached at process level (_SCHEMA_READY_FLAGS), not just
    st.session_state: session state resets on every new browser
    session/tab, which meant every dispatcher's first page load of the day
    paid for a fresh column_exists() round trip even though the process
    already confirmed the schema minutes earlier for someone else. A
    Streamlit worker process serves many sessions, so a module-level flag
    is the right scope for "has this process already confirmed the schema
    is ready" - it still self-heals (re-runs the idempotent ALTER/CREATE
    INDEX chain) on a fresh worker that has never checked.
    """
    if "fast_triage" in _SCHEMA_READY_FLAGS or st.session_state.get("_operations_fast_triage_schema_ready"):
        _SCHEMA_READY_FLAGS.add("fast_triage")
        return

    if column_exists("order_intake", "llm_review_reason"):
        _SCHEMA_READY_FLAGS.add("fast_triage")
        st.session_state["_operations_fast_triage_schema_ready"] = True
        return

    execute("alter table order_intake add column if not exists triage_status text not null default 'Not Triaged'")
    execute("alter table order_intake add column if not exists triage_engine text")
    execute("alter table order_intake add column if not exists triage_reason text")
    execute("alter table order_intake add column if not exists triage_tags jsonb not null default '[]'::jsonb")
    execute("alter table order_intake add column if not exists triaged_at timestamptz")
    execute("alter table order_intake add column if not exists work_level text")
    execute("alter table order_intake add column if not exists department_lane text")
    execute("alter table order_intake add column if not exists work_queue text")
    execute("alter table order_intake add column if not exists llm_review_required boolean not null default false")
    execute("alter table order_intake add column if not exists llm_review_reason text")

    execute("create index if not exists idx_order_intake_triage_status on order_intake(triage_status)")
    execute("create index if not exists idx_order_intake_work_level on order_intake(work_level)")
    execute("create index if not exists idx_order_intake_work_queue on order_intake(work_queue)")
    execute("create index if not exists idx_order_intake_llm_review_required on order_intake(llm_review_required)")

    _SCHEMA_READY_FLAGS.add("fast_triage")
    st.session_state["_operations_fast_triage_schema_ready"] = True

def ensure_operations_email_sync_schema() -> None:
    if "email_sync" in _SCHEMA_READY_FLAGS or st.session_state.get("_operations_email_sync_schema_ready"):
        _SCHEMA_READY_FLAGS.add("email_sync")
        ensure_operations_fast_triage_schema()
        ensure_operations_case_schema()
        return

    if column_exists("order_intake", "case_id"):
        _SCHEMA_READY_FLAGS.add("email_sync")
        st.session_state["_operations_email_sync_schema_ready"] = True
        ensure_operations_fast_triage_schema()
        ensure_operations_case_schema()
        return

    execute("alter table order_intake add column if not exists email_direction text not null default 'inbound'")
    execute("alter table order_intake add column if not exists email_mailbox text")
    execute("alter table order_intake add column if not exists email_in_reply_to text")
    execute("alter table order_intake add column if not exists email_references jsonb not null default '[]'::jsonb")
    execute("alter table order_intake add column if not exists email_thread_id text")
    execute("alter table order_intake add column if not exists email_normalized_subject text")
    execute("alter table order_intake add column if not exists conversation_status text not null default 'New Conversation'")
    execute("alter table order_intake add column if not exists email_synced_at timestamptz")
    execute("alter table order_intake add column if not exists case_id bigint")

    ensure_operations_fast_triage_schema()

    execute("create index if not exists idx_order_intake_email_thread_id on order_intake(email_thread_id)")
    execute("create index if not exists idx_order_intake_email_normalized_subject on order_intake(email_normalized_subject)")
    execute("create index if not exists idx_order_intake_conversation_status on order_intake(conversation_status)")
    execute("create index if not exists idx_order_intake_email_direction on order_intake(email_direction)")
    execute("create index if not exists idx_order_intake_email_mailbox on order_intake(email_mailbox)")
    execute("create index if not exists idx_order_intake_case_id on order_intake(case_id)")

    ensure_operations_case_schema()

    _SCHEMA_READY_FLAGS.add("email_sync")
    st.session_state["_operations_email_sync_schema_ready"] = True


def inbox_review_where_clause() -> str:
    terminal = ", ".join([f"'{status}'" for status in INBOX_TERMINAL_REVIEW_STATUSES])
    return f"where coalesce(review_status, 'Open') not in ({terminal})"


@st.cache_data(show_spinner=False, ttl=30)
def load_operations_inbox_df(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.triage_status,
            oi.triage_engine,
            oi.triage_reason,
            oi.triage_tags,
            oi.triaged_at,
            oi.work_level,
            oi.department_lane,
            oi.work_queue,
            oi.llm_review_required,
            oi.llm_review_reason,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            left(coalesce(oi.raw_text, ''), 1200) as raw_text_preview,
            case
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as pdf_count,
            case
                when jsonb_typeof(oi.parsed_data -> :attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :attachments_key)
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as attachment_count,
            case
                when coalesce(oi.parsed_data #>> '{{_email_sync,source_attachment_count}}', '') ~ '^[0-9]+$'
                    then (oi.parsed_data #>> '{{_email_sync,source_attachment_count}}')::int
                else 0
            end as source_attachment_count,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from (
            select *
            from order_intake
            {where_clause}
        ) oi
        left join operations_cases oc on oc.id = oi.case_id
        order by oi.created_at desc
        """,
        {
            "pdf_attachments_key": OPERATIONS_PDF_ATTACHMENTS_KEY,
            "attachments_key": OPERATIONS_ATTACHMENTS_KEY,
        },
    )


@st.cache_data(show_spinner=False, ttl=30)
def load_operations_inbox_record(intake_id: int) -> pd.DataFrame:
    return read_df(
        """
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.triage_status,
            oi.triage_engine,
            oi.triage_reason,
            oi.triage_tags,
            oi.triaged_at,
            oi.work_level,
            oi.department_lane,
            oi.work_queue,
            oi.llm_review_required,
            oi.llm_review_reason,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            oi.raw_text,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from order_intake oi
        left join operations_cases oc on oc.id = oi.case_id
        where oi.id = :intake_id
        limit 1
        """,
        {"intake_id": int(intake_id)},
    )


def load_operations_inbox_record_set(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            id,
            source_subject,
            source_sender,
            source_message_id,
            email_direction,
            email_thread_id,
            email_normalized_subject,
            conversation_status,
            triage_status,
            triage_engine,
            triage_reason,
            triage_tags,
            triaged_at,
            work_level,
            department_lane,
            work_queue,
            llm_review_required,
            llm_review_reason,
            parsed_data,
            raw_text,
            request_type,
            conversation_key,
            matched_load_id,
            case_id,
            confidence_score,
            action_required
        from order_intake
        {where_clause}
        order by created_at desc
        """
    )



def fast_triage_from_parsed(parsed_data: dict | str | None) -> dict:
    parsed = coerce_json_dict(parsed_data)
    triage = parsed.get("_fast_triage")
    return triage if isinstance(triage, dict) else {}


def fast_triage_for_record(record) -> dict:
    if not hasattr(record, "get"):
        return {}

    parsed_triage = fast_triage_from_parsed(record.get("parsed_data"))
    triage = dict(parsed_triage)

    for source_key, triage_key in [
        ("triage_status", "status"),
        ("triage_engine", "engine"),
        ("triage_reason", "triage_reason"),
        ("work_level", "work_level"),
        ("department_lane", "department_lane"),
        ("work_queue", "work_queue"),
        ("llm_review_required", "llm_required"),
        ("llm_review_reason", "llm_reason"),
    ]:
        value = record.get(source_key)
        if safe_str(value) and triage_key not in triage:
            triage[triage_key] = value

    return triage

def apply_fast_triage_to_intake(
    intake_id: int,
    triage: dict,
    parsed_data: dict | None = None,
    *,
    subject: str = "",
    body: str = "",
) -> None:
    parsed_data = parsed_data if isinstance(parsed_data, dict) else {}

    if not subject or not body:
        try:
            source_df = read_df(
                """
                select
                    coalesce(source_subject, '') as source_subject,
                    coalesce(raw_text, '') as raw_text
                from order_intake
                where id = :intake_id
                limit 1
                """,
                {"intake_id": int(intake_id)},
            )

            if source_df is not None and not source_df.empty:
                source_record = source_df.iloc[0].to_dict()

                if not subject:
                    subject = safe_str(
                        source_record.get("source_subject", "")
                    )

                if not body:
                    raw_text = safe_str(
                        source_record.get("raw_text", "")
                    )
                    body = (
                        extract_latest_email_body(raw_text)
                        or raw_text
                    )
        except Exception:
            pass

    triage = enforce_authoritative_booking_triage(
        subject=subject,
        body=body,
        parsed=parsed_data,
        triage=triage,
    )
    triage = enforce_container_quantity_mismatch_review(parsed_data, triage)

    parsed_data["_fast_triage"] = triage or {}

    execute(
        """
        update order_intake
        set request_type = :request_type,
            conversation_key = coalesce(:conversation_key, conversation_key),
            matched_load_id = coalesce(:matched_load_id, matched_load_id),
            confidence_score = :confidence_score,
            action_required = coalesce(:action_required, action_required),
            parsed_data = cast(:parsed_data as jsonb),
            triage_status = :triage_status,
            triage_engine = :triage_engine,
            triage_reason = :triage_reason,
            triage_tags = cast(:triage_tags as jsonb),
            triaged_at = now(),
            work_level = :work_level,
            department_lane = :department_lane,
            work_queue = :work_queue,
            llm_review_required = :llm_review_required,
            llm_review_reason = :llm_review_reason
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "request_type": safe_str(triage.get("request_type")) or "Customer Request",
            "conversation_key": safe_str(triage.get("conversation_key")) or None,
            "matched_load_id": int_or_none(triage.get("matched_load_id")),
            "confidence_score": int(float(triage.get("confidence_score", 0) or 0)),
            "action_required": safe_str(triage.get("triage_reason")) or safe_str(triage.get("action_required")) or None,
            "parsed_data": json_dump(parsed_data),
            "triage_status": safe_str(triage.get("status")) or "Complete",
            "triage_engine": safe_str(triage.get("engine")) or "fast_rules_v1",
            "triage_reason": safe_str(triage.get("triage_reason")),
            "triage_tags": json.dumps(triage.get("tags") or []),
            "work_level": safe_str(triage.get("work_level")),
            "department_lane": safe_str(triage.get("department_lane")),
            "work_queue": safe_str(triage.get("work_queue")),
            "llm_review_required": bool(triage.get("llm_required") or triage.get("llm_review_required")),
            "llm_review_reason": safe_str(triage.get("llm_reason")) or safe_str(triage.get("llm_review_reason")),
        },
    )

def update_intake_classification(
    intake_id: int,
    request_type: str,
    conversation_key: str,
    matched_load_id,
    confidence_score: int,
    action_required: str | None = None,
) -> None:
    execute(
        """
        update order_intake
        set request_type = :request_type,
            conversation_key = :conversation_key,
            matched_load_id = :matched_load_id,
            confidence_score = :confidence_score,
            action_required = coalesce(:action_required, action_required),
            triage_status = case when triage_status = 'Not Triaged' then 'Manual' else triage_status end
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "request_type": request_type,
            "conversation_key": conversation_key or None,
            "matched_load_id": matched_load_id,
            "confidence_score": int(confidence_score or 0),
            "action_required": action_required,
        },
    )


def create_load_from_inbox_item(
    work_item_id: int,
    approved_fields: dict[str, Any],
    *,
    conversation_key: str = "",
    request_type: str = "New Booking",
    subject: str = "",
    sender: str = "",
    body: str = "",
    case_id=None,
    case_priority: str = "Normal",
) -> dict:
    """Create a load from an inbox work item and keep the handoff linked."""
    load_id = create_load_from_intake(int(work_item_id), approved_fields)
    execute(
        """
        update order_intake
        set review_status = 'Order Created',
            matched_load_id = :load_id,
            request_type = :request_type,
            conversation_key = coalesce(nullif(:conversation_key, ''), conversation_key)
        where id = :intake_id
        """,
        {
            "intake_id": int(work_item_id),
            "load_id": load_id,
            "request_type": request_type or "New Booking",
            "conversation_key": conversation_key,
        },
    )
    if subject or body:
        _save_load_communication(
            load_id,
            int(work_item_id),
            conversation_key,
            request_type or "New Booking",
            subject,
            sender,
            body,
            direction="inbound",
            case_id=case_id,
        )
    resolved_case_id = int_or_none(case_id)
    if resolved_case_id is not None:
        update_operations_case(
            case_id=resolved_case_id,
            status="Attached to Load",
            owner="Dispatch",
            priority=safe_str(case_priority) or "Normal",
            linked_load_id=load_id,
            next_action=f"New load {load_id} created from Operations Inbox.",
        )
        add_operations_case_note(resolved_case_id, f"Created new load {load_id} from request #{work_item_id}.")
    return {"load_id": load_id, "review_status": "Order Created"}


def update_load_from_inbox_item(
    work_item_id: int,
    load_id,
    approved_fields: dict[str, Any],
    *,
    fill_blank_only: bool = True,
    conversation_key: str = "",
    request_type: str = "Booking Update",
    subject: str = "",
    sender: str = "",
    body: str = "",
    case_id=None,
    case_owner: str = "Dispatch",
    case_priority: str = "Normal",
) -> dict:
    """Update a matched load from an inbox work item and save the communication."""
    resolved_load_id = int_or_none(load_id)
    if resolved_load_id is None:
        return {
            "load_id": None,
            "updated_fields": [],
            "skipped_fields": [],
            "error": "Missing matched load id.",
        }

    update_result = update_load_from_operations_pdf(
        resolved_load_id,
        approved_fields,
        overwrite=not fill_blank_only,
    )
    if not isinstance(update_result, dict):
        update_result = {"updated_fields": [], "skipped_fields": [], "error": ""}

    _save_load_communication(
        resolved_load_id,
        int(work_item_id),
        conversation_key,
        request_type or "Booking Update",
        subject,
        sender,
        body,
        direction="inbound",
        case_id=case_id,
    )
    execute(
        """
        update order_intake
        set review_status = 'Attached',
            matched_load_id = :matched_load_id,
            request_type = :request_type,
            conversation_key = coalesce(nullif(:conversation_key, ''), conversation_key)
        where id = :intake_id
        """,
        {
            "intake_id": int(work_item_id),
            "matched_load_id": resolved_load_id,
            "request_type": request_type or "Booking Update",
            "conversation_key": conversation_key,
        },
    )
    resolved_case_id = int_or_none(case_id)
    if resolved_case_id is not None:
        next_action = f"Request attached to load {resolved_load_id}."
        updated_fields = update_result.get("updated_fields") or []
        if updated_fields:
            next_action += " Updated fields: " + ", ".join(updated_fields)
        update_operations_case(
            case_id=resolved_case_id,
            status="Attached to Load",
            owner=safe_str(case_owner) or "Dispatch",
            priority=safe_str(case_priority) or "Normal",
            linked_load_id=resolved_load_id,
            next_action=next_action,
        )
    update_result["load_id"] = resolved_load_id
    return update_result


def store_operations_parsed_data(intake_id: int, parsed_data: dict, action_required: str | None = None) -> None:
    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            action_required = coalesce(:action_required, action_required)
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": json_dump(parsed_data),
            "action_required": action_required,
        },
    )


def order_action_required_from_parsed(parsed: dict) -> str:
    missing = []
    for field in ["Booking Number", "Customer", "Warehouse"]:
        if not safe_str(parsed.get(field, "")):
            missing.append(field)

    if missing:
        return "Missing order details: " + ", ".join(missing)

    return "New booking details found; review parsed fields before creating order."


# Compatibility aliases
_inbox_review_where_clause = inbox_review_where_clause
_load_operations_inbox_df = load_operations_inbox_df
_load_operations_inbox_record = load_operations_inbox_record
_load_operations_inbox_record_set = load_operations_inbox_record_set
_update_intake_classification = update_intake_classification
_fast_triage_from_parsed = fast_triage_from_parsed
_fast_triage_for_record = fast_triage_for_record
_apply_fast_triage_to_intake = apply_fast_triage_to_intake
_store_operations_parsed_data = store_operations_parsed_data
_order_action_required_from_parsed = order_action_required_from_parsed


# ============================================================
# Conversation / timeline
# ============================================================

GENERIC_CONVERSATION_KEYS = {
    "contact",
    "customer-request",
    "customer request",
    "unknown",
    "none",
    "null",
}


def is_generic_conversation_key(value: str) -> bool:
    normalized = safe_str(value).strip().lower()
    return (
        not normalized
        or normalized in GENERIC_CONVERSATION_KEYS
        or normalized.startswith(("email-", "intake-"))
    )


def row_email_references(row) -> list[str]:
    references = row.get("email_references", []) if hasattr(row, "get") else []
    if isinstance(references, str):
        try:
            references = json.loads(references)
        except Exception:
            references = []
    if not isinstance(references, list):
        return []
    return [safe_str(value) for value in references if safe_str(value)]


def normalized_customer_subject_key(row) -> str:
    sender = safe_str(row.get("source_sender", "") if hasattr(row, "get") else "")
    customer = safe_str(row.get("case_customer", "") if hasattr(row, "get") else "") or sender
    customer = re.sub(r"\s*<[^>]+>\s*", "", customer).strip().lower()
    subject = (
        safe_str(row.get("email_normalized_subject", "") if hasattr(row, "get") else "")
        or safe_str(row.get("source_subject", "") if hasattr(row, "get") else "")
    )
    subject = re.sub(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", "", subject, flags=re.I)
    subject = re.sub(r"\s+", " ", subject).strip().lower()
    if not customer or not subject:
        return ""
    return f"{customer}|{subject}"


def row_conversation_join_key(row) -> str:
    for key in ["email_thread_id", "email_in_reply_to"]:
        value = safe_str(row.get(key, "") if hasattr(row, "get") else "")
        if value:
            return value

    references = row_email_references(row)
    if references:
        return references[0]

    source_message_id = safe_str(row.get("source_message_id", "") if hasattr(row, "get") else "")
    if source_message_id:
        return source_message_id

    conversation_key = safe_str(row.get("conversation_key", "") if hasattr(row, "get") else "")
    if conversation_key and not is_generic_conversation_key(conversation_key):
        return conversation_key

    parsed = coerce_json_dict(row.get("parsed_data", {}) if hasattr(row, "get") else {})
    text = " ".join(
        [
            safe_str(row.get("source_subject", "") if hasattr(row, "get") else ""),
            safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else ""),
            safe_str(row.get("raw_text", "") if hasattr(row, "get") else ""),
            str(parsed),
        ]
    )
    tokens = extract_reference_tokens(text)
    for value in [
        parsed.get("Booking Number", ""),
        parsed.get("Container Number", ""),
        parsed.get("Reference Number", ""),
        tokens.get("booking_number", ""),
        tokens.get("container_number", ""),
        tokens.get("reference_number", ""),
    ]:
        normalized = safe_str(value)
        if normalized:
            return normalized

    customer_subject_key = normalized_customer_subject_key(row)
    if customer_subject_key:
        return customer_subject_key

    row_id = safe_str(row.get("id", "") if hasattr(row, "get") else "")
    return f"intake:{row_id}" if row_id else ""


@st.cache_data(show_spinner=False, ttl=30)
def load_operations_conversation_summary_df() -> pd.DataFrame:
    key_expr = conversation_join_expr()
    try:
        return read_df(
            f"""
            select
                {key_expr} as conversation_join_key,
                count(*) as conversation_message_count,
                max(source_received_at) as last_message_at,
                (array_agg(coalesce(email_direction, 'inbound') order by source_received_at desc nulls last, created_at desc))[1] as latest_direction,
                (array_agg(coalesce(source_sender, '') order by source_received_at desc nulls last, created_at desc))[1] as latest_sender,
                (array_agg(coalesce(conversation_status, 'New Conversation') order by source_received_at desc nulls last, created_at desc))[1] as latest_conversation_status,
                max(case when coalesce(email_direction, 'inbound') = 'inbound' then source_received_at end) as last_inbound_at,
                max(case when coalesce(email_direction, 'inbound') = 'outbound' then source_received_at end) as last_outbound_at,
                max(matched_load_id) as thread_matched_load_id
            from order_intake
            where {operations_email_source_filter()}
            group by {key_expr}
            """
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False, ttl=30)
def load_operations_conversation_timeline(conversation_key: str) -> pd.DataFrame:
    if not safe_str(conversation_key):
        return pd.DataFrame()

    key_expr = conversation_join_expr()
    try:
        return read_df(
            f"""
            select
                id,
                source_received_at,
                created_at,
                coalesce(email_direction, 'inbound') as email_direction,
                coalesce(email_mailbox, '') as email_mailbox,
                coalesce(source_sender, '') as source_sender,
                coalesce(source_subject, '') as source_subject,
                coalesce(source_message_id, '') as source_message_id,
                coalesce(email_thread_id, '') as email_thread_id,
                coalesce(conversation_key, '') as conversation_key,
                matched_load_id,
                parsed_data,
                coalesce(conversation_status, 'New Conversation') as conversation_status,
                coalesce(review_status, 'Open') as review_status,
                left(coalesce(raw_text, ''), 1200) as message_preview
            from order_intake
            where {operations_email_source_filter()}
              and {key_expr} = :conversation_key
            order by coalesce(source_received_at, created_at) asc, id asc
            """,
            {"conversation_key": conversation_key},
        )
    except Exception:
        return pd.DataFrame()


def timeline_filter_tokens(record, tokens: dict, subject: str, body: str) -> set[str]:
    parsed = coerce_json_dict(record.get("parsed_data") if hasattr(record, "get") else {})
    candidates = [
        tokens.get("booking_number", ""),
        tokens.get("container_number", ""),
        tokens.get("reference_number", ""),
        parsed.get("Booking Number", ""),
        parsed.get("Container Number", ""),
        parsed.get("Reference Number", ""),
        record.get("matched_load_id", "") if hasattr(record, "get") else "",
    ]
    text_tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    candidates.extend([
        text_tokens.get("booking_number", ""),
        text_tokens.get("container_number", ""),
        text_tokens.get("reference_number", ""),
    ])
    return {safe_str(value).upper() for value in candidates if len(safe_str(value)) >= 4}


def filter_operations_timeline_for_record(
    timeline_df: pd.DataFrame,
    record,
    tokens: dict,
    subject: str,
    body: str,
) -> pd.DataFrame:
    if timeline_df.empty:
        return timeline_df

    selected_id = int_or_none(record.get("id") if hasattr(record, "get") else None)
    selected_thread = safe_str(record.get("email_thread_id", "") if hasattr(record, "get") else "")
    selected_message_id = safe_str(record.get("source_message_id", "") if hasattr(record, "get") else "")
    token_values = timeline_filter_tokens(record, tokens, subject, body)

    if not token_values:
        return timeline_df

    def row_matches(row) -> bool:
        row_id = int_or_none(row.get("id"))
        if selected_id is not None and row_id == selected_id:
            return True
        if selected_message_id and safe_str(row.get("source_message_id", "")) == selected_message_id:
            return True

        haystack = " ".join(
            [
                safe_str(row.get("source_subject", "")),
                safe_str(row.get("message_preview", "")),
                safe_str(row.get("conversation_key", "")),
                safe_str(row.get("parsed_data", "")),
                safe_str(row.get("matched_load_id", "")),
            ]
        ).upper()

        if any(value and value in haystack for value in token_values):
            return True

        if selected_thread and safe_str(row.get("email_thread_id", "")) == selected_thread:
            row_tokens = extract_reference_tokens(haystack)
            row_token_values = {safe_str(value).upper() for value in row_tokens.values() if safe_str(value)}
            return bool(token_values & row_token_values)

        return False

    filtered = timeline_df[timeline_df.apply(row_matches, axis=1)].copy()
    return filtered if not filtered.empty else timeline_df


# Compatibility aliases
_row_conversation_join_key = row_conversation_join_key
_load_operations_conversation_summary_df = load_operations_conversation_summary_df
_load_operations_conversation_timeline = load_operations_conversation_timeline
_filter_operations_timeline_for_record = filter_operations_timeline_for_record


# ============================================================
# Reference extraction / classification
# ============================================================

def normalize_reference_token(value: str) -> str:
    return re.sub(r"\s+", "-", str(value or "").strip(" :#-")).upper()


def extract_reference_tokens(text: str) -> dict:
    fields = extract_operational_fields(newest_message=str(text or ""))["fields"]
    booking_value = safe_str(fields.get("Booking Number"))
    container_value = safe_str(fields.get("Container Number")).upper()
    ref_value = safe_str(fields.get("Reference Number"))

    return {
        "booking_number": normalize_reference_token(booking_value) if booking_value else "",
        "container_number": container_value,
        "reference_number": normalize_reference_token(ref_value) if ref_value else "",
    }


def extract_reference_from_text(text: str) -> str:
    tokens = extract_reference_tokens(text)
    return (
        tokens.get("booking_number")
        or tokens.get("container_number")
        or tokens.get("reference_number")
        or ""
    )


def subject_is_reply(subject: str) -> bool:
    return bool(re.match(r"^\s*(?:re|fw|fwd)\s*:", safe_str(subject), re.I))


def is_information_update(text: str) -> bool:
    lowered = str(text or "").lower()

    if contains_any(lowered, INFORMATION_UPDATE_TERMS):
        return True

    if re.search(r"\bplease\s+have\b.{0,80}\b(?:container|load|truck)\b", lowered, re.I):
        return True

    if re.search(r"\b(?:about|around|at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", lowered, re.I):
        return True

    return bool(re.search(r"\b(?:please\s+note|note)\b.{0,80}\b(?:hours?|schedule|address|cutoff|lfd)\b", lowered, re.I))


def has_order_placement_signal(text: str) -> bool:
    lowered = str(text or "").lower()

    if contains_any(lowered, ORDER_PLACEMENT_TERMS):
        return True

    return bool(
        re.search(
            r"\b(?:please|pls)\b.{0,60}\b(?:arrange|book|schedule|dispatch|pickup|pick up|deliver|handle|process|create|enter)\b",
            lowered,
            re.I,
        )
    )


def detect_customer_language(subject: str, body: str) -> str:
    text = f"{subject or ''} {body or ''}".lower()
    spanish_score = sum(1 for term in SPANISH_LANGUAGE_TERMS if term in text)
    english_score = sum(1 for term in ENGLISH_LANGUAGE_TERMS if term in text)

    if spanish_score >= 2 and english_score >= 2:
        return "Bilingual"
    if spanish_score > english_score:
        return "Spanish"
    return "English"


def resolve_reply_language(reply_language: str, subject: str, body: str) -> str:
    reply_language = safe_str(reply_language) or "Auto"

    if reply_language == "Auto":
        return detect_customer_language(subject, body)

    if reply_language in REPLY_LANGUAGE_OPTIONS:
        return reply_language

    return "English"


def coerce_parsed_for_classification(subject: str, body: str, parsed: dict | None = None) -> dict:
    if isinstance(parsed, dict):
        return parsed

    try:
        return parse_email_text(subject, body)
    except Exception:
        return {}


def has_reference_details(tokens: dict, parsed: dict) -> bool:
    parsed_reference_fields = ["Booking Number", "Container Number", "Reference Number"]

    return any(
        safe_str(tokens.get(key, ""))
        for key in ["booking_number", "container_number", "reference_number"]
    ) or any(
        safe_str(parsed.get(field, ""))
        for field in parsed_reference_fields
    )


_QUOTE_INTENT_WORD_RE = re.compile(
    r"\b(?:quote|quoting|quoted|rate|pricing|price|cotiz\w*|tarifa|presupuesto)\b", re.I
)
_EQUIPMENT_SIZE_RE = re.compile(r"\b(?:20|40|45)\s*'?\s*(?:ft|hc|hq|gp|rf|std|dv)?\b", re.I)

# Words that can appear on either side of "<X> to <Y>" without being a lane -
# days/times/people-handoff/routing phrases that are structurally identical
# to "Houston to Dallas" but never a shipping origin/destination.
_LANE_STOP_WORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "am", "pm", "reply", "send", "forward", "forwarded", "respond", "back",
    "us", "me", "him", "her", "them", "you", "accounting", "dispatch",
    "support", "billing", "team", "customer", "driver",
}

_LANE_WORD = r"[A-Za-z][A-Za-z.'-]{2,}(?:\s+[A-Za-z][A-Za-z.'-]{2,}){0,2}"
# English: "<origin> to <dest>" or "<origin> → <dest>".
_LANE_EN_RE = re.compile(
    rf"(?P<origin>{_LANE_WORD})\s*(?:to|→)\s*(?P<dest>{_LANE_WORD})", re.I
)
# Spanish: "<origin> a <dest>" (optionally preceded by "de") - "a" alone is
# too ambiguous in English to ever use outside a Spanish-language context, so
# this pattern is only ever checked when a Spanish quote-intent word is
# already present in the same sentence (see _sentence_has_plausible_lane).
_LANE_ES_RE = re.compile(
    rf"(?:\bde\s+)?(?P<origin>{_LANE_WORD})\s+a\s+(?P<dest>{_LANE_WORD})", re.I
)
_SPANISH_QUOTE_WORD_RE = re.compile(r"\b(?:cotiz\w*|tarifa|presupuesto|necesito)\b", re.I)


def _lane_words_are_plausible(origin: str, dest: str) -> bool:
    origin_words = origin.lower().split()
    dest_words = dest.lower().split()
    if any(word in _LANE_STOP_WORDS for word in origin_words + dest_words):
        return False
    if re.search(r"\d", origin) or re.search(r"\d", dest):
        return False
    return True


def _sentence_has_plausible_lane(sentence: str) -> bool:
    """A "<place> to/a <place>" phrase is only trusted as an actual quote
    lane when the *same sentence* also carries a quote/rate-intent word or an
    equipment/size token - "John to Maria" and "Monday to Friday" are
    structurally identical to "Houston to Dallas" and only distinguishable by
    this kind of contextual proximity, not by capitalization (customer
    messages routinely arrive all-lowercase or all-caps)."""
    has_signal = bool(_QUOTE_INTENT_WORD_RE.search(sentence) or _EQUIPMENT_SIZE_RE.search(sentence))
    if not has_signal:
        return False

    for match in _LANE_EN_RE.finditer(sentence):
        if _lane_words_are_plausible(match.group("origin"), match.group("dest")):
            return True

    if _SPANISH_QUOTE_WORD_RE.search(sentence):
        for match in _LANE_ES_RE.finditer(sentence):
            if _lane_words_are_plausible(match.group("origin"), match.group("dest")):
                return True

    return False


_FROM_LABEL_LINE_RE = re.compile(r"(?im)^\s*(?:from|de)\s*:\s*(\S.*?)\s*$")
_TO_LABEL_LINE_RE = re.compile(r"(?im)^\s*(?:to|para)\s*:\s*(\S.*?)\s*$")


def _has_plausible_quote_lane(text: str) -> bool:
    newest_only = extract_latest_email_body(text) or text

    # A labeled operational lane ("From: Houston" / "To: Dallas") is a
    # distinct shape from prose ("Houston to Dallas") - message_scope.py
    # already keeps this out of quoted-history clipping when it's genuinely
    # active content, so here it only needs the same origin/destination
    # plausibility filter prose lanes use (rejects days/times/people).
    #
    # From:/To: are matched independently rather than requiring the To:
    # line to immediately follow the From: line - a real operational block
    # can have other recognized fields (Equipment/Subject/Booking Number/
    # dates/...) between them in any order, and message_scope.py has
    # already confirmed newest_only is one coherent active/operational
    # block by the time this runs, so pairing the first From: value found
    # with the first To: value found anywhere in it is safe.
    from_match = _FROM_LABEL_LINE_RE.search(newest_only)
    to_match = _TO_LABEL_LINE_RE.search(newest_only)
    if from_match and to_match and _lane_words_are_plausible(from_match.group(1), to_match.group(1)):
        return True

    for sentence in re.split(r"[.!?;\n]+", newest_only):
        if _sentence_has_plausible_lane(sentence):
            return True
    return False


def has_quote_details(text: str, parsed: dict, tokens: dict) -> bool:
    # All free-text checks below operate on the newest message only - a
    # quote request that only exists inside quoted/forwarded history (e.g.
    # the customer's own earlier message being replied to) must not count as
    # active quote detail for the *current* message. parsed/tokens are
    # trusted as-is here since upstream parsing already scopes them to the
    # newest message in the real intake pipeline.
    newest_text = extract_latest_email_body(text) or text
    detail_score = 0

    if safe_str(parsed.get("Port", "")):
        detail_score += 1

    if safe_str(parsed.get("Warehouse", "")) or safe_str(parsed.get("Address", "")):
        detail_score += 1

    if safe_str(parsed.get("Size", "")) or _EQUIPMENT_SIZE_RE.search(newest_text):
        detail_score += 1

    if safe_str(parsed.get("Delivery Need Date", "")) or re.search(
        r"\b(?:today|tomorrow|asap|next week|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
        newest_text,
        re.I,
    ):
        detail_score += 1

    if _has_plausible_quote_lane(newest_text):
        detail_score += 2

    if has_reference_details(tokens, parsed):
        detail_score += 1

    return detail_score >= 2


def has_new_order_details(text: str, parsed: dict, tokens: dict) -> bool:
    has_order_signal = has_order_placement_signal(text)
    has_order_document_signal = (
        contains_any(text, ["delivery order", "load order", "work order", "tender", "tendered"])
        and not is_information_update(text)
    )

    if not has_order_signal and not has_order_document_signal:
        return False

    detail_score = 0
    for field in ["Booking Number", "Customer", "Container Number", "Port", "Warehouse", "Delivery Need Date"]:
        if safe_str(parsed.get(field, "")):
            detail_score += 1

    if has_reference_details(tokens, parsed):
        detail_score += 1

    if has_order_signal or has_order_document_signal:
        detail_score += 1

    return detail_score >= 3


def operations_intent_scores(subject: str, body: str, parsed: dict | None = None) -> dict[str, int]:
    # Scope to the active message only - quote/rate/booking-intent keywords
    # inside quoted reply history or an administrative-only forward wrapper
    # must never combine with details from a *different* scope (see
    # services/message_scope.py). A forwarded-only request still contributes
    # its own (forwarded) active text here, not an empty string.
    from services.message_scope import build_message_scope

    scope = build_message_scope(body or "")
    text = f"{subject or ''} {scope.classification_text}"
    parsed = coerce_parsed_for_classification(subject, body, parsed)
    tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")

    has_reference = has_reference_details(tokens, parsed)
    info_update = is_information_update(text)
    has_order_signal = has_order_placement_signal(text)

    scores = {request_type: 0 for request_type in REQUEST_TYPES}
    scores["Customer Request"] = 20

    def add(request_type: str, points: int, condition: bool = True) -> None:
        if condition and request_type in scores:
            scores[request_type] += points

    add("Missing Information", 70, contains_any(text, MISSING_INFO_TERMS))
    add("Cancellation", 75, contains_any(text, CANCELLATION_TERMS))
    add("POD Request", 75, contains_any(text, POD_TERMS))
    add("Appointment Update", 70, contains_any(text, APPOINTMENT_INTENT_TERMS))
    add("Quote Request", 70, contains_any(text, QUOTE_INTENT_TERMS) or bool(_QUOTE_LANE_SHORTHAND_RE.search(text)))
    add("Booking Update", 60, contains_any(text, UPDATE_INTENT_TERMS))
    add("New Booking", 65, contains_any(text, NEW_ORDER_INTENT_TERMS))
    add("Billing", 75, contains_any(text, BILLING_TERMS) or has_actual_billing_request(subject, body))
    add("Driver Issue", 70, contains_any(text, DRIVER_ISSUE_TERMS))
    add("Port Issue", 70, contains_any(text, PORT_ISSUE_TERMS))
    add("Spam/Marketing", 85, contains_any(text, SPAM_MARKETING_TERMS))
    add("Booking Update", 45, info_update and has_reference)
    add("Customer Request", 35, info_update and not has_reference)

    if has_reference:
        for request_type in ["Booking Update", "Appointment Update", "POD Request", "Cancellation", "Billing", "Driver Issue", "Port Issue"]:
            add(request_type, 18)
    else:
        for request_type in ["Booking Update", "Appointment Update", "POD Request", "Cancellation"]:
            scores[request_type] = max(0, scores[request_type] - 35)

    if has_quote_details(text, parsed, tokens):
        add("Quote Request", 25)
    else:
        scores["Quote Request"] = max(0, scores["Quote Request"] - 30)

    if has_new_order_details(text, parsed, tokens):
        add("New Booking", 35)
    else:
        scores["New Booking"] = max(0, scores["New Booking"] - 35)

    if info_update and not has_order_signal:
        scores["New Booking"] = max(0, scores["New Booking"] - 70)
        if has_reference:
            scores["Booking Update"] = max(scores["Booking Update"], 78)
        else:
            scores["Customer Request"] = max(scores["Customer Request"], 65)

    if max(scores.values() or [0]) < 45:
        scores["Customer Request"] = max(scores["Customer Request"], 50)

    return scores


def classify_customer_request(subject: str, body: str, parsed: dict | None = None) -> str:
    from services.message_scope import build_message_scope

    scope = build_message_scope(body or "")
    text = f"{subject or ''} {scope.classification_text}"
    parsed = coerce_parsed_for_classification(subject, body, parsed)
    tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = has_reference_details(tokens, parsed)

    if is_booking_confirmation(subject, body, parsed) and not has_actual_billing_request(subject, body):
        return "New Booking"

    if is_information_update(text) and not has_order_placement_signal(text):
        return "Booking Update" if has_reference else "Customer Request"

    scores = operations_intent_scores(subject, body, parsed)
    scored_types = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type = scored_types[0][0] if scored_types else "Customer Request"

    if contains_any(text, MISSING_INFO_TERMS):
        return "Missing Information"

    if best_type == "Spam/Marketing":
        return "Spam/Marketing"

    if best_type == "Quote Request":
        return "Quote Request" if has_quote_details(text, parsed, tokens) else "Customer Request"

    if best_type == "New Booking":
        return "New Booking" if has_new_order_details(text, parsed, tokens) else "Customer Request"

    if best_type in ["Cancellation", "POD Request", "Appointment Update", "Booking Update"]:
        return best_type if has_reference else "Customer Request"

    if best_type in ["Billing", "Driver Issue", "Port Issue"]:
        return best_type

    return best_type if best_type in REQUEST_TYPES else "Customer Request"


def action_required_for_request(
    request_type: str,
    parsed: dict,
    body: str,
    subject: str = "",
    tokens: dict | None = None,
    matched_load_id=None,
) -> str:
    text = f"{subject or ''} {body or ''}"
    tokens = tokens or extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = has_reference_details(tokens, parsed)

    if request_type == "Customer Request":
        if contains_any(text, UPDATE_INTENT_TERMS) and not has_reference:
            return "Customer is asking for an update but did not include booking, container, or reference. Reply for identifying details."
        if (
            contains_any(text, QUOTE_INTENT_TERMS) or _QUOTE_LANE_SHORTHAND_RE.search(text)
        ) and not has_quote_details(text, parsed, tokens):
            return "Customer may need pricing but did not include enough lane details. Reply for pickup, delivery, equipment, and date."
        if contains_any(text, NEW_ORDER_INTENT_TERMS) and not has_new_order_details(text, parsed, tokens):
            return "Customer may be sending an order but key load details are missing. Reply for the load order or booking details."
        return "Review customer question and reply from the inbox."

    if request_type == "Quote Request":
        if not has_quote_details(text, parsed, tokens):
            return "Quote intent found, but lane/equipment/date details are missing. Reply before creating a quote."
        return "Quote details found; review lane, timing, and equipment before sending rate."

    if request_type == "Missing Information":
        return "Customer needs missing information; reply or attach to the matching order."

    if request_type == "Cancellation":
        if matched_load_id is None:
            return "Cancellation requested without a matched order. Ask for booking, container, or reference before cancelling."
        return "Customer requested cancellation; verify matching order before cancelling."

    if request_type == "Appointment Update":
        if matched_load_id is None:
            return "Appointment request needs booking, container, or reference before updating the schedule."
        return "Appointment update received; attach to matching order and update schedule."

    if request_type == "Booking Update":
        if matched_load_id is None:
            return "Update request needs booking, container, or reference before attaching to an order."
        return "Update matched to an order; attach it and update order notes or schedule."

    if request_type == "POD Request":
        if matched_load_id is None:
            return "POD request needs booking, container, or reference before sending documents."
        return "POD request matched to an order; verify POD status and reply."

    if request_type == "Billing":
        if matched_load_id is None:
            return "Billing question needs booking, invoice, container, or reference before billing can respond."
        return "Billing request matched to a load; review invoice/POD status and route to Billing."

    if request_type == "Business Communication":
        return "Route to the responsible business department and keep it out of dispatcher load queues."

    if request_type == "No Action / FYI":
        return "No shipment action needed. Archive after review so the message remains searchable."

    if request_type == "Driver Issue":
        if matched_load_id is None:
            return "Driver issue needs booking, container, truck, or reference before dispatch can resolve it."
        return "Driver issue matched to a load; dispatch should review driver, truck, appointment, and status."

    if request_type == "Port Issue":
        if matched_load_id is None:
            return "Port or terminal issue needs booking, container, or reference before dispatch can resolve it."
        return "Port issue matched to a load; review terminal/hold/gate details and update the customer."

    if request_type == "Spam/Marketing":
        return "Marketing or non-operational email; close unless it needs management review."

    if request_type == "New Booking":
        return order_action_required_from_parsed(parsed)

    if body:
        return "Review message and choose the next action."

    return "Review imported email."


def classification_confidence(
    request_type: str,
    subject: str,
    body: str,
    parsed: dict,
    tokens: dict,
    matched_load_id,
    match_confidence: int,
) -> int:
    text = f"{subject or ''} {body or ''}"

    if matched_load_id is not None:
        return max(match_confidence, 90)

    if request_type == "Customer Request":
        if (
            (contains_any(text, UPDATE_INTENT_TERMS) and not has_reference_details(tokens, parsed))
            or (contains_any(text, QUOTE_INTENT_TERMS) and not has_quote_details(text, parsed, tokens))
            or (contains_any(text, NEW_ORDER_INTENT_TERMS) and not has_new_order_details(text, parsed, tokens))
        ):
            return 60
        return 70

    if request_type == "Quote Request":
        return 80 if has_quote_details(text, parsed, tokens) else 55

    if request_type == "New Booking":
        return 80 if has_new_order_details(text, parsed, tokens) else 55

    if request_type in ["Appointment Update", "Booking Update", "Cancellation", "POD Request", "Billing", "Driver Issue", "Port Issue"]:
        return 75 if has_reference_details(tokens, parsed) else 55

    if request_type == "Missing Information":
        return 75

    if request_type == "Spam/Marketing":
        return 90

    if request_type in {"Business Communication", "No Action / FYI"}:
        return 80

    return max(match_confidence, 50)


def build_operations_email_classification(
    subject: str,
    body: str,
    parsed: dict | None = None,
    fallback_key: str = "",
    sender: str = "",
) -> dict:
    parsed = coerce_parsed_for_classification(subject, body, parsed)
    intent_scores = operations_intent_scores(subject, body, parsed)
    detected_type = classify_customer_request(subject, body, parsed)
    tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")

    load_match_candidates = find_load_match_candidates(
        tokens,
        parsed=parsed,
        subject=subject,
        body=body,
        limit=5,
    )

    matched_load_id, match_confidence = find_matching_load(
        tokens,
        parsed=parsed,
        subject=subject,
        body=body,
    )

    confidence = classification_confidence(
        detected_type,
        subject,
        body,
        parsed,
        tokens,
        matched_load_id,
        match_confidence,
    )

    if intent_scores:
        top_score = int(max(intent_scores.values()))
        confidence = max(confidence, min(95, top_score))
    if parsed.get("_confidence") is not None:
        try:
            confidence = min(confidence, int(round(float(parsed["_confidence"]) * 100)))
        except (TypeError, ValueError):
            pass

    conversation_key = (
        tokens.get("booking_number")
        or tokens.get("container_number")
        or tokens.get("reference_number")
        or fallback_key
        or "customer-request"
    )

    action_required = action_required_for_request(
        detected_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=matched_load_id,
    )

    return {
        "request_type": detected_type,
        "tokens": tokens,
        "matched_load_id": matched_load_id,
        "confidence_score": confidence,
        "conversation_key": conversation_key,
        "action_required": action_required,
        "intent_scores": intent_scores,
        "load_match_candidates": load_match_candidates,
    }


def operations_classification_for_review(
    record,
    parsed: dict,
    subject: str,
    body: str,
    fallback_key: str,
) -> dict:
    saved_type = safe_str(record.get("request_type", ""))
    saved_confidence = pd.to_numeric(record.get("confidence_score", 0), errors="coerce")
    if pd.isna(saved_confidence):
        saved_confidence = 0

    saved_match = record.get("matched_load_id")
    saved_matched_load_id = None

    if pd.notna(saved_match) and safe_str(saved_match):
        try:
            saved_matched_load_id = int(saved_match)
        except Exception:
            saved_matched_load_id = None

    saved_is_usable = (
        saved_type in REQUEST_TYPES
        and saved_type not in ["Needs Classification", "Other"]
        and (int(saved_confidence) >= 70 or saved_matched_load_id is not None)
    )

    if not saved_is_usable:
        return build_operations_email_classification(
            subject,
            body,
            parsed,
            fallback_key=fallback_key,
            sender=safe_str(record.get("source_sender", "")),
        )

    tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    conversation_key = (
        safe_str(record.get("conversation_key", ""))
        or tokens.get("booking_number")
        or tokens.get("container_number")
        or tokens.get("reference_number")
        or fallback_key
        or "customer-request"
    )

    action_required = safe_str(record.get("action_required", "")) or action_required_for_request(
        saved_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=saved_matched_load_id,
    )

    return {
        "request_type": saved_type,
        "tokens": tokens,
        "matched_load_id": saved_matched_load_id,
        "confidence_score": int(saved_confidence),
        "conversation_key": conversation_key,
        "action_required": action_required,
    }


# Compatibility aliases
_normalize_reference_token = normalize_reference_token
_extract_reference_tokens = extract_reference_tokens
_extract_reference_from_text = extract_reference_from_text
_subject_is_reply = subject_is_reply
_is_information_update = is_information_update
_has_order_placement_signal = has_order_placement_signal
_detect_customer_language = detect_customer_language
_resolve_reply_language = resolve_reply_language
_coerce_parsed_for_classification = coerce_parsed_for_classification
_has_reference_details = has_reference_details
_has_quote_details = has_quote_details
_has_new_order_details = has_new_order_details
_operations_intent_scores = operations_intent_scores
_action_required_for_request = action_required_for_request
_classification_confidence = classification_confidence
_build_operations_email_classification = build_operations_email_classification
_operations_classification_for_review = operations_classification_for_review


# ============================================================
# Row routing / queue logic
# ============================================================

def operations_parsed_for_row(row) -> dict:
    return coerce_json_dict(row.get("parsed_data") if hasattr(row, "get") else {})


def operations_reference_tokens_for_row(row) -> dict:
    parsed = operations_parsed_for_row(row)
    return extract_reference_tokens(
        "\n".join(
            [
                safe_str(row.get("source_subject", "") if hasattr(row, "get") else ""),
                safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else ""),
                safe_str(parsed),
            ]
        )
    )


def operations_has_matched_load(row) -> bool:
    value = row.get("matched_load_id", "") if hasattr(row, "get") else ""

    if value is None:
        return False

    try:
        if pd.isna(value):
            return False
    except Exception:
        pass

    return safe_str(value).lower() not in {"", "nan", "none", "null"}


def operations_row_text(row) -> str:
    if not hasattr(row, "get"):
        return ""

    return "\n".join(
        [
            safe_str(row.get("source_subject", "")),
            safe_str(row.get("raw_text_preview", "")),
            safe_str(row.get("raw_text", "")),
            safe_str(row.get("action_required", "")),
        ]
    )


def effective_operations_request_type_for_row(row) -> str:
    saved_type = safe_str(row.get("request_type", "") if hasattr(row, "get") else "") or "Needs Classification"

    if saved_type != "New Booking":
        return saved_type

    subject = safe_str(row.get("source_subject", "") if hasattr(row, "get") else "")
    body = safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else "") or safe_str(
        row.get("raw_text", "") if hasattr(row, "get") else ""
    )

    text = f"{subject}\n{body}"
    parsed = operations_parsed_for_row(row)
    tokens = extract_reference_tokens(f"{text}\n{parsed}")
    has_reference = has_reference_details(tokens, parsed)

    if is_information_update(text) and not has_order_placement_signal(text):
        return "Booking Update" if has_reference else "Customer Request"

    if subject_is_reply(subject) and not has_new_order_details(text, parsed, tokens):
        return "Booking Update" if has_reference else "Customer Request"

    return saved_type


def operations_attachment_status_for_row(row) -> str:
    try:
        saved_count = int(float(row.get("attachment_count", 0) or 0)) if hasattr(row, "get") else 0
    except Exception:
        saved_count = 0

    try:
        source_count = int(float(row.get("source_attachment_count", 0) or 0)) if hasattr(row, "get") else 0
    except Exception:
        source_count = 0

    subject = safe_str(row.get("source_subject", "") if hasattr(row, "get") else "")
    preview = safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else "")

    if saved_count > 0:
        return f"Saved {saved_count}"

    if source_count > 0:
        return f"Mailbox {source_count}"

    if message_mentions_attachment(subject, preview):
        return "Mentioned"

    return "None"


def message_mentions_attachment(subject: str, body: str) -> bool:
    return bool(
        re.search(
            r"\b(attached|attachment|attachments|adjunto|adjuntos|pdf|doc(?:ument)?|delivery order|packing list|imo|bol)\b",
            f"{subject or ''}\n{body or ''}",
            re.I,
        )
    )


def normalize_operations_owner(value: str) -> str:
    owner = safe_str(value)
    if owner == "Customer Service":
        return "Customer"
    if owner == "Manager":
        return "Operations"
    return owner


def operations_owner_label_for_row(row) -> str:
    saved_owner = normalize_operations_owner(row.get("case_owner", "") if hasattr(row, "get") else "")
    if saved_owner and saved_owner != "Unassigned":
        return saved_owner

    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = operations_row_text(row).lower()

    if request_type == "Billing" or any(term in text for term in ["invoice", "billing", "detention", "demurrage", "rate"]):
        return "Billing"

    if request_type == "Driver Issue" or any(term in text for term in ["driver", "truck", "chassis"]):
        return "Driver"

    if request_type == "Port Issue" or any(term in text for term in ["port", "terminal", "gate", "steamship"]):
        return "Port"

    if request_type in {"Customer Request", "Missing Information"}:
        return "Customer"

    if request_type in {"Spam/Marketing", "Cancellation", "POD Request"}:
        return "Operations"

    return "Dispatch"


def operations_priority_label_for_row(row) -> str:
    saved_priority = safe_str(row.get("case_priority", "") if hasattr(row, "get") else "")
    priority_map = {
        "Urgent": "Critical",
        "Normal": "Medium",
        "Critical": "Critical",
        "High": "High",
        "Medium": "Medium",
        "Low": "Low",
    }

    if saved_priority in priority_map:
        return priority_map[saved_priority]

    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = operations_row_text(row).lower()

    critical_terms = [
        "urgent",
        "asap",
        "critical",
        "lfd today",
        "last free day today",
        "driver waiting",
        "driver stuck",
        "no show",
        "gate closed",
        "truck down",
    ]

    high_terms = [
        "lfd",
        "last free day",
        "cutoff",
        "appointment today",
        "same day",
        "hold",
        "released",
        "available now",
        "cancel",
    ]

    if any(term in text for term in critical_terms):
        return "Critical"

    if any(term in text for term in high_terms) or request_type in {"Cancellation", "Driver Issue", "Port Issue"}:
        return "High"

    if request_type in {"Billing", "Spam/Marketing"}:
        return "Low"

    return "Medium"


def operations_status_label_for_row(row) -> str:
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    action_required = safe_str(row.get("action_required", "") if hasattr(row, "get") else "")
    review_status = safe_str(row.get("review_status_clean", "") if hasattr(row, "get") else "")
    case_status = safe_str(row.get("case_status", "") if hasattr(row, "get") else "")

    try:
        confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    except Exception:
        confidence = 0

    has_match = operations_has_matched_load(row)

    if request_type == "Missing Information" or "missing" in action_required.lower():
        return "Needs Details"

    if (
        not has_match
        and confidence < 70
        and request_type in {
            "New Booking",
            "Booking Update",
            "Appointment Update",
            "Quote Request",
            "Cancellation",
            "POD Request",
            "Billing",
            "Driver Issue",
            "Port Issue",
        }
    ):
        return "Needs Details"

    if case_status:
        return case_status

    if review_status in {"Waiting on Customer", "Waiting Customer"}:
        return "Waiting Customer"

    if review_status:
        return review_status

    return "Open"


def operations_document_signal_for_row(row) -> bool:
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")

    if request_type == "POD Request":
        return True

    text = operations_row_text(row).lower()

    document_terms = [
        "pod",
        "proof of delivery",
        "delivery order",
        "bill of lading",
        "bol",
        "packing list",
        "hazmat",
        "imo",
        "document",
        "documents attached",
        "attachment contains",
    ]

    if any(term in text for term in document_terms):
        return True

    attachment_status = safe_str(row.get("attachment_status", "") if hasattr(row, "get") else "")
    return attachment_status.startswith(("Saved", "Mailbox")) and request_type not in {"New Booking", "Quote Request"}


def operations_work_queue_for_row(row) -> str:
    triage = fast_triage_for_record(row)
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    if request_type == "Quote Request":
        return "Quotes"

    saved_queue = safe_str(row.get("work_queue", "") if hasattr(row, "get") else "") or safe_str(triage.get("work_queue"))
    if saved_queue:
        return saved_queue

    status_label = safe_str(row.get("status_label", "") if hasattr(row, "get") else "")
    review_status = safe_str(row.get("review_status_clean", "") if hasattr(row, "get") else "")

    try:
        confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    except Exception:
        confidence = 0

    has_match = operations_has_matched_load(row)

    if request_type in {"No Action / FYI", "Spam/Marketing"}:
        return "Archive"

    if request_type == "Business Communication":
        return "Business"

    if status_label.startswith("Waiting") or review_status in {"Waiting on Customer", "Waiting Customer"}:
        return "Waiting"

    if request_type == "Billing" or operations_owner_label_for_row(row) == "Billing":
        return "Billing"

    if request_type in {"Needs Classification", "Other", ""} or confidence < 50:
        return "Review"

    if operations_document_signal_for_row(row):
        return "Documents"

    if request_type == "New Booking":
        return "New Orders"

    if has_match or request_type in {"Booking Update", "Appointment Update", "Cancellation", "Driver Issue", "Port Issue"}:
        return "Existing Loads"

    if request_type in {"Customer Request", "Missing Information"}:
        return "Action Required"

    return "Action Required"


def operations_sender_is_vip(row) -> bool:
    sender = safe_str(row.get("source_sender", "") if hasattr(row, "get") else "").lower()

    if not sender or "@" not in sender:
        return False

    domain = sender.rsplit("@", 1)[-1]

    return any(domain == vip_domain or domain.endswith(f".{vip_domain}") for vip_domain in VIP_OPERATIONS_DOMAINS)


def operations_has_shipment_signal(row) -> bool:
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")

    if operations_has_matched_load(row) or request_type in OPERATIONAL_REQUEST_TYPES:
        return True

    parsed = operations_parsed_for_row(row)
    tokens = operations_reference_tokens_for_row(row)

    return has_reference_details(tokens, parsed) or operations_document_signal_for_row(row)


def operations_control_level_for_row(row) -> str:
    triage = fast_triage_for_record(row)
    saved_level = safe_str(row.get("work_level", "") if hasattr(row, "get") else "") or safe_str(triage.get("work_level"))
    if saved_level in OPERATIONS_CONTROL_LEVELS:
        return saved_level

    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")

    try:
        confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    except Exception:
        confidence = 0

    text = operations_row_text(row).lower()
    is_vip = operations_sender_is_vip(row)
    has_shipment_signal = operations_has_shipment_signal(row)
    owner = operations_owner_label_for_row(row)

    if request_type in NO_ACTION_REQUEST_TYPES:
        return "Level 3 - No Action / Archive"

    if contains_any(text, NO_ACTION_COMMUNICATION_TERMS + SPAM_MARKETING_TERMS) and not is_vip and not has_shipment_signal:
        return "Level 3 - No Action / Archive"

    if request_type in {"", "Other", "Needs Classification"} or confidence < 50:
        return "Needs Review"

    if request_type in BUSINESS_REQUEST_TYPES:
        return "Level 2 - Business Communications"

    if owner in {"Billing", "Manager", "Safety"} and not has_shipment_signal:
        return "Level 2 - Business Communications"

    if contains_any(text, BUSINESS_COMMUNICATION_TERMS) and not has_shipment_signal:
        return "Level 2 - Business Communications"

    return "Level 1 - Operational Cases"


def operations_department_lane_for_row(row) -> str:
    triage = fast_triage_for_record(row)
    saved_department = safe_str(row.get("department_lane", "") if hasattr(row, "get") else "") or safe_str(triage.get("department_lane"))
    if saved_department:
        return saved_department

    level = safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or operations_control_level_for_row(row)
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = operations_row_text(row).lower()
    owner = operations_owner_label_for_row(row)

    if level == "Needs Review":
        return "Human Review"

    if level == "Level 3 - No Action / Archive":
        if request_type == "Spam/Marketing" or contains_any(text, SPAM_MARKETING_TERMS):
            return "Spam"
        if "duplicate" in text:
            return "Duplicates"
        return "Archive / FYI"

    if level == "Level 2 - Business Communications":
        if request_type == "Billing" or contains_any(text, BILLING_TERMS):
            return "Accounting"
        if any(term in text for term in ["insurance", "legal", "contract", "agreement", "claim", "bank", "utility"]):
            return "Management"
        if any(term in text for term in ["recruiting", "resume", "candidate", "employment", "hr", "human resources"]):
            return "Management"
        if any(term in text for term in ["new customer inquiry", "sales lead", "credit application"]):
            return "Sales"
        if any(term in text for term in ["vendor", "supplier", "software", "it support", "password"]):
            return "Management"
        return owner if owner not in {"Dispatch", "Customer", "Driver", "Port", "Warehouse"} else "Management"

    if request_type == "Billing" or owner == "Billing":
        return "Accounting"

    if owner in {"Driver", "Port", "Warehouse"}:
        return "Dispatch"

    if owner == "Customer":
        return "Customer Service"

    if owner in {"Operations", "Manager"}:
        return "Operations"

    return "Dispatch"


def operations_confidence_label(score) -> str:
    try:
        score_int = int(float(score or 0))
    except Exception:
        score_int = 0

    if score_int >= 80:
        return "High Match"

    if score_int >= 60:
        return "Medium Match"

    return "Unknown"


def operations_work_item_for_row(row) -> str:
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    work_queue = safe_str(row.get("work_queue", "") if hasattr(row, "get") else "")

    if request_type == "New Booking":
        return "Create Order"
    if request_type == "Quote Request":
        return "Create Quote"
    if request_type == "Appointment Update":
        return "Appointment"
    if request_type == "Booking Update":
        return "Booking Revision"
    if request_type == "Cancellation":
        return "Cancel Booking"
    if request_type == "POD Request":
        return "POD / Documents"
    if request_type == "Billing":
        return "Billing Case"
    if request_type == "Business Communication":
        return "Business Case"
    if request_type in NO_ACTION_REQUEST_TYPES:
        return "Archive"

    if work_queue:
        return work_queue

    return request_type or "Review"


def operations_recommended_action_for_row(row) -> str:
    level = safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or operations_control_level_for_row(row)
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    work_queue = safe_str(row.get("work_queue", "") if hasattr(row, "get") else "")
    has_match = operations_has_matched_load(row)

    if level == "Needs Review":
        return "Classify"

    if level == "Level 3 - No Action / Archive":
        return "Archive / Close"

    if level == "Level 2 - Business Communications":
        lane = operations_department_lane_for_row(row)
        return f"Route to {lane}"

    if request_type == "New Booking":
        return "Create Order"

    if request_type == "Quote Request":
        return "Create Quote"

    if has_match:
        return "Attach / Update Load"

    if work_queue == "Documents":
        return "Review Documents"

    if request_type == "Missing Information":
        return "Reply for Details"

    return "Review Case"


def operations_control_reason_for_row(row) -> str:
    triage = fast_triage_for_record(row)
    triage_reason = safe_str(row.get("triage_reason", "") if hasattr(row, "get") else "") or safe_str(triage.get("triage_reason"))
    if triage_reason:
        return triage_reason

    level = safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or operations_control_level_for_row(row)
    request_type = safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    confidence_label = operations_confidence_label(row.get("confidence_score", 0) if hasattr(row, "get") else 0)

    if level == "Needs Review":
        return f"{confidence_label}; needs human routing"

    if level == "Level 2 - Business Communications":
        return f"{request_type or 'Business'} routed by department"

    if level == "Level 3 - No Action / Archive":
        return f"{request_type or 'FYI'} should not become dispatcher work"

    return f"{request_type or 'Operational'} work item"


def operations_identifier_hints_for_row(row) -> dict:
    parsed = operations_parsed_for_row(row)
    tokens = operations_reference_tokens_for_row(row)

    return {
        "booking": safe_str(parsed.get("Booking Number", "")) or tokens.get("booking_number", ""),
        "container": safe_str(parsed.get("Container Number", "")) or tokens.get("container_number", ""),
        "reference": safe_str(parsed.get("Reference Number", "")) or tokens.get("reference_number", ""),
        "customer": (
            safe_str(parsed.get("Customer", ""))
            or safe_str(row.get("case_customer", "") if hasattr(row, "get") else "")
            or safe_str(row.get("client_name", "") if hasattr(row, "get") else "")
        ),
        "warehouse": safe_str(parsed.get("Warehouse", "")) or safe_str(parsed.get("Address", "")),
        "port": safe_str(parsed.get("Port", "")),
        "delivery": safe_str(parsed.get("Delivery Need Date", "")) or safe_str(parsed.get("LFD", "")),
    }


def operations_queue_labels_for_level(level: str, level_df: pd.DataFrame) -> list[str]:
    if level == "Level 1 - Operational Cases":
        preferred = ["Action Required", "New Orders", "Quotes", "Existing Loads", "Waiting", "Documents"]
        dynamic = sorted([
            value
            for value in level_df.get("work_queue", pd.Series(dtype=str)).dropna().astype(str).unique()
            if value
        ])
        labels = [label for label in preferred if label in dynamic] + [label for label in dynamic if label not in preferred]
        return labels or preferred

    if level == "Level 2 - Business Communications":
        preferred = ["Accounting", "Management", "Sales", "Safety", "Customer Service"]
        dynamic = sorted([
            value
            for value in level_df.get("department_lane", pd.Series(dtype=str)).dropna().astype(str).unique()
            if value
        ])
        return [label for label in preferred if label in dynamic] + [label for label in dynamic if label not in preferred] or ["Business"]

    if level == "Level 3 - No Action / Archive":
        preferred = ["Archive / FYI", "Spam", "Duplicates"]
        dynamic = sorted([
            value
            for value in level_df.get("department_lane", pd.Series(dtype=str)).dropna().astype(str).unique()
            if value
        ])
        return [label for label in preferred if label in dynamic] + [label for label in dynamic if label not in preferred] or ["Archive / FYI"]

    return ["Needs Review"]


def operations_queue_mask_for_level(level_df: pd.DataFrame, level: str, queue_label: str) -> pd.Series:
    if level == "Level 1 - Operational Cases":
        return level_df["work_queue"].eq(queue_label)

    if level in {"Level 2 - Business Communications", "Level 3 - No Action / Archive"}:
        return level_df["department_lane"].eq(queue_label)

    return pd.Series(True, index=level_df.index)


def collapse_operations_inbox_to_cases(inbox_df: pd.DataFrame) -> pd.DataFrame:
    if inbox_df.empty:
        return inbox_df

    collapsed = inbox_df.copy()

    collapsed["case_row_key"] = collapsed.apply(
        lambda row: f"case-{int(float(row.get('case_id')))}"
        if int_or_none(row.get("case_id")) is not None
        else f"intake-{int(row.get('id'))}",
        axis=1,
    )

    collapsed["_case_sort_time"] = pd.to_datetime(
        collapsed.get("case_last_message_at", collapsed.get("source_received_at")),
        errors="coerce",
    )

    fallback_sort_time = pd.to_datetime(collapsed.get("source_received_at"), errors="coerce").fillna(
        pd.to_datetime(collapsed.get("created_at"), errors="coerce")
    )

    collapsed["_case_sort_time"] = collapsed["_case_sort_time"].fillna(fallback_sort_time)

    collapsed = (
        collapsed.sort_values(["case_row_key", "_case_sort_time", "id"])
        .drop_duplicates("case_row_key", keep="last")
        .sort_values("_case_sort_time", ascending=False)
        .drop(columns=["_case_sort_time"], errors="ignore")
    )

    return collapsed


def operations_items_needing_smart_group_update(inbox_df: pd.DataFrame) -> pd.Series:
    if inbox_df.empty:
        return pd.Series(dtype=bool)

    current_type = inbox_df["request_type"].fillna("").astype(str).str.strip()
    triage_status = inbox_df.get("triage_status", pd.Series("", index=inbox_df.index)).fillna("").astype(str).str.strip()
    confidence = pd.to_numeric(inbox_df["confidence_score"], errors="coerce").fillna(0)

    has_match = (
        inbox_df["matched_load_id"].notna()
        & ~inbox_df["matched_load_id"].astype(str).isin(["", "nan", "None"])
    )

    subject_series = inbox_df["source_subject"].fillna("").astype(str) if "source_subject" in inbox_df.columns else pd.Series("", index=inbox_df.index)

    body_series = (
        inbox_df["raw_text"].fillna("").astype(str).apply(extract_latest_email_body)
        if "raw_text" in inbox_df.columns
        else pd.Series("", index=inbox_df.index)
    )

    message_series = subject_series + "\n" + body_series

    obvious_info_new_booking = current_type.eq("New Booking") & message_series.apply(
        lambda value: is_information_update(value) and not has_order_placement_signal(value)
    )

    action_type_needs_reference = current_type.isin([
        "New Booking",
        "Booking Update",
        "Appointment Update",
        "Quote Request",
        "Cancellation",
        "POD Request",
        "Billing",
        "Driver Issue",
        "Port Issue",
    ]) & ~has_match & confidence.lt(70)

    return (
        triage_status.isin(["", "Not Triaged"])
        | current_type.isin(["", "Needs Classification", "Other", "Spam/Marketing"])
        | action_type_needs_reference
        | obvious_info_new_booking
    )


def operations_can_open_case(row, request_type: str = "") -> bool:
    level = safe_str(row.get("control_level", "") if hasattr(row, "get") else "")
    request_type = safe_str(request_type) or safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")

    if level != "Level 1 - Operational Cases":
        return False

    if request_type in NO_ACTION_REQUEST_TYPES or request_type in BUSINESS_REQUEST_TYPES:
        return False

    return request_type in OPERATIONAL_REQUEST_TYPES or operations_has_shipment_signal(row)


# Compatibility aliases
_operations_parsed_for_row = operations_parsed_for_row
_operations_reference_tokens_for_row = operations_reference_tokens_for_row
_operations_has_matched_load = operations_has_matched_load
_operations_row_text = operations_row_text
_effective_operations_request_type_for_row = effective_operations_request_type_for_row
_operations_attachment_status_for_row = operations_attachment_status_for_row
_operations_owner_label_for_row = operations_owner_label_for_row
_operations_priority_label_for_row = operations_priority_label_for_row
_operations_status_label_for_row = operations_status_label_for_row
_operations_document_signal_for_row = operations_document_signal_for_row
_operations_work_queue_for_row = operations_work_queue_for_row
_operations_sender_is_vip = operations_sender_is_vip
_operations_has_shipment_signal = operations_has_shipment_signal
_operations_control_level_for_row = operations_control_level_for_row
_operations_department_lane_for_row = operations_department_lane_for_row
_operations_confidence_label = operations_confidence_label
_operations_work_item_for_row = operations_work_item_for_row
_operations_recommended_action_for_row = operations_recommended_action_for_row
_operations_control_reason_for_row = operations_control_reason_for_row
_operations_identifier_hints_for_row = operations_identifier_hints_for_row
_operations_queue_labels_for_level = operations_queue_labels_for_level
_operations_queue_mask_for_level = operations_queue_mask_for_level
_collapse_operations_inbox_to_cases = collapse_operations_inbox_to_cases
_operations_items_needing_smart_group_update = operations_items_needing_smart_group_update
_operations_can_open_case = operations_can_open_case
_ensure_operations_fast_triage_schema = ensure_operations_fast_triage_schema


# ============================================================
# Review context
# ============================================================

def build_operations_review_context(
    *,
    record,
    parsed: dict,
    subject: str,
    body: str,
    selected_id: int,
) -> dict:
    classification = operations_classification_for_review(
        record,
        parsed,
        subject,
        body,
        fallback_key=f"intake-{selected_id}",
    )

    detected_type = classification.get("request_type")
    tokens = classification.get("tokens", {})
    matched_load_id = classification.get("matched_load_id")
    confidence = classification.get("confidence_score", 0)
    conversation_key = classification.get("conversation_key")

    saved_matched_load_id = record.get("matched_load_id")

    if matched_load_id is None and pd.notna(saved_matched_load_id) and safe_str(saved_matched_load_id):
        try:
            matched_load_id = int(saved_matched_load_id)
            classification["matched_load_id"] = matched_load_id
        except Exception:
            pass

    can_open_operations_case = operations_can_open_case(record, detected_type)

    operations_case = (
        load_operations_case_by_id(record.get("case_id"))
        if can_open_operations_case and record.get("case_id")
        else {}
    )

    cleared_case_fields = {}
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
            cleared_case_fields[record_field] = None

    case_record_updates = {}
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
            case_record_updates[record_field] = operations_case.get(case_field)

        case_load_id = int_or_none(operations_case.get("linked_load_id"))
        if matched_load_id is None and case_load_id is not None:
            matched_load_id = case_load_id
            classification["matched_load_id"] = matched_load_id

    viewed_key = None
    if operations_case:
        viewed_key = f"operations_case_viewed_{operations_case.get('id')}_{selected_id}"

    return {
        "classification": classification,
        "detected_type": detected_type,
        "tokens": tokens,
        "matched_load_id": matched_load_id,
        "confidence": confidence,
        "conversation_key": conversation_key,
        "can_open_operations_case": can_open_operations_case,
        "operations_case": operations_case,
        "cleared_case_fields": cleared_case_fields,
        "case_record_updates": case_record_updates,
        "viewed_key": viewed_key,
    }


# ============================================================
# Auto-classification / metrics
# ============================================================

def auto_classify_open_inbox_items(
    limit: int | pd.DataFrame = 25,
    time_budget_seconds: int | float | None = None,
) -> dict:
    """Run fast non-LLM triage on a small batch of open inbox items.

    This function intentionally works in bounded batches.  Rechecking every
    historical Operations Inbox row in one Streamlit click can hold the app open
    long enough for the browser/WebSocket to time out.  Call it repeatedly with
    a small limit when you need to backfill old mail.

    Backward compatible: callers may pass an integer limit or a prefiltered
    DataFrame. This does not open Operations Cases and does not call the LLM.
    """
    ensure_operations_email_sync_schema()

    if time_budget_seconds is None:
        try:
            time_budget_seconds = float(_get_app_setting("OPERATIONS_TRIAGE_TIME_BUDGET_SECONDS", 8) or 8)
        except Exception:
            time_budget_seconds = 8

    started = time.perf_counter()
    result = {
        "checked": 0,
        "updated": 0,
        "errors": 0,
        "llm_required": 0,
        "store_only": 0,
        "remaining_estimate": None,
        "stopped_early": False,
        "elapsed_seconds": None,
        "error_messages": [],
    }

    if isinstance(limit, pd.DataFrame):
        inbox_df = limit.copy()
        try:
            configured_limit = int(_get_app_setting("OPERATIONS_TRIAGE_BATCH_LIMIT", 25) or 25)
        except Exception:
            configured_limit = 25
        max_rows = max(1, min(int(len(inbox_df)), configured_limit))
    else:
        where_clause = inbox_review_where_clause()
        inbox_df = load_operations_inbox_record_set(where_clause)
        max_rows = max(1, min(int(limit or 25), 100))

    if inbox_df.empty:
        result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        return result

    needs_update = operations_items_needing_smart_group_update(inbox_df)
    candidates = inbox_df[needs_update].copy()
    result["remaining_estimate"] = int(len(candidates))
    work_df = candidates.head(max_rows).copy()

    for _, row in work_df.iterrows():
        if time_budget_seconds and (time.perf_counter() - started) >= float(time_budget_seconds):
            result["stopped_early"] = True
            break

        result["checked"] += 1

        try:
            record = row.to_dict()
            parsed = coerce_json_dict(record.get("parsed_data"))
            subject = safe_str(record.get("source_subject", ""))
            raw_text = safe_str(record.get("raw_text", ""))

            # Keep batch backfills cheap.  The deep agent/manual review can still
            # inspect the complete body for a selected email.
            body = extract_latest_email_body(raw_text[:20000]) or raw_text[:20000]

            classification = build_operations_email_classification(
                subject=subject,
                body=body,
                parsed=parsed,
                fallback_key=f"intake-{record.get('id')}",
                sender=safe_str(record.get("source_sender", "")),
            )

            triage = triage_operations_email(
                sender=safe_str(record.get("source_sender", "")),
                subject=subject,
                body=body,
                parsed=parsed,
                attachments=[],
                direction=safe_str(record.get("email_direction", "inbound")),
                mailbox=safe_str(record.get("email_mailbox", "")),
                classification=classification,
            )
            triage = enforce_authoritative_booking_triage(
                subject=subject,
                body=body,
                parsed=parsed,
                triage=triage,
            )
            triage = enforce_container_quantity_mismatch_review(parsed, triage)
            if not triage.get("conversation_key"):
                triage["conversation_key"] = classification.get("conversation_key", "")
            if triage.get("matched_load_id") is None:
                triage["matched_load_id"] = classification.get("matched_load_id")

            apply_fast_triage_to_intake(int(record["id"]), triage, parsed)

            result["updated"] += 1
            if triage.get("llm_required"):
                result["llm_required"] += 1
            if triage.get("store_only"):
                result["store_only"] += 1

        except Exception as exc:
            result["errors"] += 1
            if len(result["error_messages"]) < 5:
                result["error_messages"].append(str(exc))

    if result["remaining_estimate"] is not None:
        result["remaining_estimate"] = max(0, int(result["remaining_estimate"]) - int(result["updated"]))
    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)

    try:
        refresh_data()
    except Exception:
        pass
    return result

def operations_inbox_status_counts() -> pd.DataFrame:
    try:
        return read_df(
            f"""
            select
                coalesce(review_status, 'Open') as review_status,
                count(*) as email_count
            from order_intake
            where {operations_email_source_filter()}
            group by coalesce(review_status, 'Open')
            order by email_count desc, review_status
            """
        )
    except Exception:
        return pd.DataFrame()


def operations_email_sync_metrics() -> dict:
    metrics = {
        "inbound": 0,
        "outbound": 0,
        "threads": 0,
        "last_sync": "",
    }

    try:
        sync_df = read_df(
            f"""
            select
                coalesce(email_direction, 'inbound') as email_direction,
                count(*) as email_count,
                count(distinct nullif(email_thread_id, '')) as thread_count,
                max(email_synced_at) as last_sync
            from order_intake
            where {operations_email_source_filter()}
            group by coalesce(email_direction, 'inbound')
            """
        )
    except Exception:
        return metrics

    if sync_df.empty:
        return metrics

    metrics["threads"] = int(sync_df["thread_count"].fillna(0).sum())

    last_sync = pd.to_datetime(sync_df["last_sync"], errors="coerce").max()
    if pd.notna(last_sync):
        metrics["last_sync"] = last_sync.strftime("%Y-%m-%d %I:%M %p")

    for _, row in sync_df.iterrows():
        direction = safe_str(row.get("email_direction", "")).lower() or "inbound"
        if direction in ["inbound", "outbound"]:
            metrics[direction] = int(row.get("email_count", 0) or 0)

    return metrics


def render_no_open_inbox_explanation() -> None:
    result = st.session_state.get("operations_email_import_result") or {}
    fetched = int(result.get("fetched", 0) or 0)
    imported = int(result.get("imported", 0) or 0)
    skipped = int(result.get("skipped", 0) or 0)

    if fetched > 0 and imported == 0 and skipped > 0:
        st.info(
            "The recent email scan found emails that are already saved in Operations Inbox. "
            "Nothing new was added, so if no rows appear here, those saved requests may already be closed, attached, "
            "converted to orders/quotes, or otherwise filtered out of the open inbox."
        )

    status_counts = operations_inbox_status_counts()
    if not status_counts.empty:
        st.caption("Saved operations email status counts")
        st.dataframe(status_counts, use_container_width=True, hide_index=True)


def recent_operations_ai_feedback_examples(limit: int = 6) -> list[dict]:
    try:
        feedback_df = read_df(
            """
            select
                correction_type,
                source_subject,
                ai_request_type,
                final_request_type,
                ai_action_required,
                final_action_required,
                ai_reply_body,
                final_reply_body,
                feedback_notes,
                created_at
            from operations_ai_feedback
            order by created_at desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return []

    examples = []
    for _, row in feedback_df.iterrows():
        examples.append(row.to_dict())

    return examples


def create_quote_request_from_intake(intake_id: int, parsed: dict, notes: str = "") -> None:
    execute(
        """
        insert into quote_requests (
            intake_id,
            customer,
            origin,
            destination,
            container_type,
            requested_date,
            notes,
            quote_status
        )
        values (
            :intake_id,
            :customer,
            :origin,
            :destination,
            :container_type,
            :requested_date,
            :notes,
            'Requested'
        )
        """,
        {
            "intake_id": int(intake_id),
            "customer": parsed.get("Customer"),
            "origin": parsed.get("Port"),
            "destination": parsed.get("Warehouse"),
            "container_type": parsed.get("Size"),
            "requested_date": parsed.get("Delivery Need Date") or None,
            "notes": notes,
        },
    )


# Compatibility aliases
_auto_classify_open_inbox_items = auto_classify_open_inbox_items
_operations_inbox_status_counts = operations_inbox_status_counts
_operations_email_sync_metrics = operations_email_sync_metrics
_render_no_open_inbox_explanation = render_no_open_inbox_explanation
_recent_operations_ai_feedback_examples = recent_operations_ai_feedback_examples
_create_quote_request_from_intake = create_quote_request_from_intake


# ============================================================
# Remaining not-yet-extracted placeholders
# ============================================================

def _email_sync_source_for_direction(direction: str) -> str:
    return "operations_email_sent" if safe_str(direction).lower() == "outbound" else "operations_email"


def _email_sync_unique_message_id(message: dict) -> str:
    """Build a stable source_message_id for database dedupe.

    Most emails have a real Message-ID. If one is missing, Yahoo/IMAP may only
    return a numeric mailbox UID, so include the mailbox to avoid collisions
    across dispatch/accounting/manager mailboxes.
    """
    message_id = safe_str(message.get("message_id"))
    mailbox = safe_str(message.get("mailbox")) or safe_str(message.get("mailbox_account"))
    imap_id = safe_str(message.get("id"))

    if message_id and not message_id.isdigit():
        return message_id.lower()

    if mailbox and imap_id:
        return f"{mailbox}:{imap_id}".lower()

    if message_id:
        return message_id.lower()

    received_at = safe_str(message.get("received_at")) or safe_str(message.get("date"))
    subject = safe_str(message.get("subject"))[:120].lower()
    sender = safe_str(message.get("from"))[:120].lower()
    return f"fallback:{mailbox}:{received_at}:{sender}:{subject}".lower()


def _email_sync_dedupe_key(subject: str, sender: str, received_at: str | None) -> str:
    return "|".join(
        [
            safe_str(subject).lower(),
            safe_str(sender).lower(),
            safe_str(received_at).lower(),
        ]
    )


def find_existing_operations_email_record(
    message_id: str,
    subject: str = "",
    sender: str = "",
    received_at: str | None = None,
) -> dict | None:
    message_id = safe_str(message_id).lower()
    subject = safe_str(subject)
    sender = safe_str(sender)
    received_at = safe_str(received_at)

    try:
        if message_id:
            existing_df = read_df(
                f"""
                select id, source_message_id, source_subject, source_sender, source_received_at
                from order_intake
                where {operations_email_source_filter()}
                  and lower(coalesce(source_message_id, '')) = :message_id
                order by id desc
                limit 1
                """,
                {"message_id": message_id},
            )
            if not existing_df.empty:
                return existing_df.iloc[0].to_dict()

        if subject and sender and received_at:
            existing_df = read_df(
                f"""
                select id, source_message_id, source_subject, source_sender, source_received_at
                from order_intake
                where {operations_email_source_filter()}
                  and coalesce(source_subject, '') = :subject
                  and coalesce(source_sender, '') = :sender
                  and coalesce(source_received_at::text, '') = :received_at
                order by id desc
                limit 1
                """,
                {"subject": subject, "sender": sender, "received_at": received_at},
            )
            if not existing_df.empty:
                return existing_df.iloc[0].to_dict()
    except Exception:
        return None

    return None


def load_existing_operations_email_lookup(limit: int = 5000) -> dict:
    lookup = {
        "loaded": False,
        "by_message_id": {},
        "by_thread_id": {},
        "by_normalized_subject": {},
        "by_received": {},
        "by_subject_sender_no_received": {},
    }

    try:
        existing_df = read_df(
            f"""
            select
                id,
                coalesce(source_message_id, '') as source_message_id,
                coalesce(email_thread_id, '') as email_thread_id,
                coalesce(email_normalized_subject, '') as email_normalized_subject,
                coalesce(source_subject, '') as source_subject,
                coalesce(source_sender, '') as source_sender,
                coalesce(source_received_at::text, '') as source_received_at
            from order_intake
            where {operations_email_source_filter()}
            order by created_at desc
            limit :limit
            """,
            {"limit": int(limit or 5000)},
        )
    except Exception:
        return lookup

    for _, row in existing_df.iterrows():
        row_dict = row.to_dict()
        message_id = safe_str(row.get("source_message_id")).lower()
        if message_id:
            lookup["by_message_id"][message_id] = row_dict

        thread_id = safe_str(row.get("email_thread_id")).lower()
        if thread_id:
            lookup["by_thread_id"].setdefault(thread_id, []).append(row_dict)

        normalized_subject = safe_str(row.get("email_normalized_subject")).lower()
        if normalized_subject:
            lookup["by_normalized_subject"].setdefault(normalized_subject, []).append(row_dict)

        received_key = _email_sync_dedupe_key(
            row.get("source_subject"),
            row.get("source_sender"),
            row.get("source_received_at"),
        )
        if received_key.strip("|"):
            lookup["by_received"][received_key] = row_dict

        no_received_key = _email_sync_dedupe_key(
            row.get("source_subject"),
            row.get("source_sender"),
            "",
        )
        if no_received_key.strip("|"):
            lookup["by_subject_sender_no_received"].setdefault(no_received_key, []).append(row_dict)

    lookup["loaded"] = True
    return lookup


def operations_email_already_imported(
    message_id: str,
    subject: str = "",
    sender: str = "",
    received_at: str | None = None,
    lookup: dict | None = None,
) -> bool:
    message_id = safe_str(message_id).lower()
    if lookup and lookup.get("loaded"):
        if message_id and message_id in lookup.get("by_message_id", {}):
            return True

        received_key = _email_sync_dedupe_key(subject, sender, received_at)
        if received_key.strip("|") and received_key in lookup.get("by_received", {}):
            return True

    return find_existing_operations_email_record(message_id, subject, sender, received_at) is not None


def _save_operations_email_attachments(message: dict, message_id: str) -> list[dict]:
    saved_attachments = []
    seen_hashes: set[str] = set()
    for index, attachment in enumerate(message.get("attachments") or [], start=1):
        content = attachment.get("content")
        if not content:
            continue
        content_sha256 = hashlib.sha256(content).hexdigest()
        if content_sha256 in seen_hashes:
            continue
        seen_hashes.add(content_sha256)
        try:
            saved = save_operations_attachment(
                content=content,
                filename=safe_str(attachment.get("filename")) or f"attachment_{index}",
                message_id=message_id or "operations_email",
                attachment_index=index,
                content_type=safe_str(attachment.get("content_type")),
            )
            saved["original_filename"] = (
                safe_str(attachment.get("filename")) or f"attachment_{index}"
            )
            saved["source_message_id"] = message_id
            saved["source_mime_part_id"] = (
                safe_str(attachment.get("content_id"))
                or safe_str(attachment.get("part_id"))
                or str(index)
            )
            saved["content_sha256"] = (
                safe_str(saved.get("content_sha256")) or content_sha256
            )
            saved_attachments.append(saved)
        except Exception as exc:
            saved_attachments.append(
                {
                    "filename": safe_str(attachment.get("filename")) or f"attachment_{index}",
                    "content_type": safe_str(attachment.get("content_type")),
                    "parse_error": f"Attachment could not be saved: {exc}",
                    "fields_found": 0,
                    "size_bytes": len(content or b""),
                }
            )
    return saved_attachments


def _prepare_operations_email_record(message: dict) -> dict:
    """Pure (DB-write-free) half of email insert processing: parsing,
    attachment saving, classification, and triage. Each step is isolated so
    a failure in any one of them (a bad attachment, a classifier exception)
    cannot prevent the raw email fields from being available to the caller
    for an insert - only that step's contribution is degraded, and the
    failure is recorded in processing_errors instead of being swallowed."""
    from services.message_scope import build_message_scope

    subject = safe_str(message.get("subject")) or "(no subject)"
    sender = safe_str(message.get("from")) or safe_str(message.get("sender")) or "(unknown sender)"
    received_at = safe_str(message.get("received_at")) or None
    raw_body = safe_str(message.get("body"))
    latest_body = extract_latest_email_body(raw_body)

    # A recognized reply/forward structure that produces empty authoritative
    # text (segmentation_status != "ok") must never be silently replaced by
    # the raw body as if it were active content (Invariant 7) - that body
    # can still contain old quoted booking confirmations, cancelled
    # requests, or another customer's history the structure deliberately
    # excluded. Falling back to raw_body is only safe when there was no
    # recognized structure to begin with (segmentation_status == "ok"),
    # in which case an empty latest_body only ever means the raw body
    # itself was blank.
    segmentation_scope = build_message_scope(raw_body)
    segmentation_collapsed = bool(raw_body) and segmentation_scope.segmentation_status != "ok"
    if not latest_body and not segmentation_collapsed:
        latest_body = raw_body
    direction = safe_str(message.get("direction")) or "inbound"
    message_id = _email_sync_unique_message_id(message)

    processing_errors: list[str] = []
    if segmentation_collapsed:
        # latest_body is deliberately left empty here (not raw_body) so
        # parse_email_text below cannot manufacture trusted-looking active-
        # message fields (a Booking Number, a cancellation instruction, ...)
        # from ambiguous or structurally-collapsed content (Invariant 8).
        # The raw body is preserved separately, in parsed_data only, purely
        # for audit/display - never as a field callers would treat as a
        # confirmed current-message value.
        processing_errors.append(
            f"segmentation {segmentation_scope.segmentation_status}: recognized reply/forward "
            "structure produced no authoritative active text - raw body withheld from parsing "
            "and preserved for audit only, routed to manual review"
        )

    try:
        parsed = parse_email_text(subject, latest_body, sender)
    except Exception as exc:
        parsed = {}
        processing_errors.append(f"parse_email_text failed: {exc}")
    if segmentation_collapsed:
        parsed["_segmentation_status"] = segmentation_scope.segmentation_status
        parsed["_segmentation_collapsed_raw_body"] = raw_body[:5000]
    parsed["_email_parsed"] = {
        key: value
        for key, value in parsed.items()
        if key != "_email_parsed"
    }

    try:
        saved_attachments = _save_operations_email_attachments(message, message_id)
    except Exception as exc:
        saved_attachments = []
        processing_errors.append(f"attachment processing failed: {exc}")

    if saved_attachments:
        try:
            # force=True: `parsed` here only ever holds this same message's
            # own weak email-body guesses (e.g. Customer inferred from the
            # sender's domain) - never a separately dispatcher-confirmed
            # value - so the specialized document/PDF parser must be allowed
            # to win per the documented parsing precedence (specialized
            # document parser > generic email-body parser).
            parsed = merge_saved_attachment_fields(parsed, saved_attachments, force=True)
        except Exception as exc:
            processing_errors.append(f"attachment field merge failed: {exc}")

    reconciliation = parsed.get("_reconciliation") if isinstance(parsed.get("_reconciliation"), dict) else {}
    if processing_errors:
        parsed["_parser_failures"] = list(processing_errors)
    review_state = derive_review_state(
        parsed,
        conflicts=list(
            dict.fromkeys(
                [
                    *(reconciliation.get("conflicts") or []),
                    *(parsed.get("_candidate_conflicts") or []),
                ]
            )
        ),
        parser_failures=processing_errors,
    )
    parsed["_needs_review"] = review_state["needs_review"]
    parsed["_confidence"] = review_state["confidence"]
    parsed["_review_reasons"] = review_state

    sync_metadata = {
        "source": "imap",
        "direction": direction,
        "mailbox": safe_str(message.get("mailbox")),
        "mailbox_account": safe_str(message.get("mailbox_account")),
        "mailbox_folder": safe_str(message.get("mailbox_folder")),
        "imap_id": safe_str(message.get("id")),
        "source_attachment_count": len(message.get("attachments") or []),
        "saved_attachment_count": len(saved_attachments),
    }
    parsed["_email_sync"] = sync_metadata

    fallback_key = (
        safe_str(message.get("conversation_key"))
        or safe_str(message.get("thread_id"))
        or message_id
    )

    try:
        classification = build_operations_email_classification(
            subject=subject,
            body=latest_body,
            parsed=parsed,
            fallback_key=fallback_key,
            sender=sender,
        )
    except Exception as exc:
        classification = {}
        processing_errors.append(f"classification failed: {exc}")

    try:
        triage = triage_operations_email(
            sender=sender,
            subject=subject,
            body=latest_body,
            parsed=parsed,
            attachments=saved_attachments or message.get("attachments") or [],
            direction=direction,
            mailbox=safe_str(message.get("mailbox")),
            classification=classification,
        )
        triage = enforce_authoritative_booking_triage(
            subject=subject,
            body=latest_body,
            parsed=parsed,
            triage=triage,
            already_matched_load=classification.get("matched_load_id") is not None,
        )
        triage = enforce_container_quantity_mismatch_review(parsed, triage)
        if review_state["needs_review"]:
            triage["llm_required"] = True
            triage["llm_review_required"] = True
            triage["llm_reason"] = "; ".join(
                [
                    *(f"Conflict: {field}" for field in review_state["conflicts"]),
                    *(f"Missing: {field}" for field in review_state["missing_required_fields"]),
                    *(f"Invalid: {field}" for field in review_state["invalid_selected_fields"]),
                    *review_state["parser_failures"],
                ]
            ) or "Validated parsing requires dispatcher review."
    except Exception as exc:
        triage = {}
        processing_errors.append(f"triage failed: {exc}")

    parsed["_fast_triage"] = triage
    if processing_errors:
        parsed["_email_sync_errors"] = processing_errors

    if safe_str(triage.get("request_type")):
        classification["request_type"] = triage.get("request_type")
    if triage.get("matched_load_id") is not None:
        classification["matched_load_id"] = triage.get("matched_load_id")
    if safe_str(triage.get("conversation_key")):
        classification["conversation_key"] = triage.get("conversation_key")
    if triage.get("confidence_score") is not None:
        classification["confidence_score"] = triage.get("confidence_score")
    if safe_str(triage.get("triage_reason")):
        classification["action_required"] = triage.get("triage_reason")

    conversation_key = (
        safe_str(message.get("conversation_key"))
        or safe_str(classification.get("conversation_key"))
        or fallback_key
    )
    thread_id = safe_str(message.get("thread_id")) or conversation_key or message_id
    normalized_subject = safe_str(message.get("normalized_subject")) or subject.lower()
    conversation_status = "Waiting Customer" if direction.lower() == "outbound" else "Customer Replied"
    matched_load_id = int_or_none(classification.get("matched_load_id"))

    for attachment in saved_attachments:
        attachment["source_subject"] = subject
        attachment["source_received_at"] = received_at
        attachment["conversation_key"] = conversation_key
        attachment["matched_load_id"] = matched_load_id
        attachment["mailbox_account"] = safe_str(message.get("mailbox_account"))
    if saved_attachments:
        parsed[OPERATIONS_ATTACHMENTS_KEY] = saved_attachments
        parsed[OPERATIONS_PDF_ATTACHMENTS_KEY] = [
            item
            for item in saved_attachments
            if is_pdf_filename(item.get("filename", ""), item.get("content_type", ""))
            or bool(item.get("is_pdf"))
        ]

    return {
        "subject": subject,
        "sender": sender,
        "received_at": received_at,
        "latest_body": latest_body,
        "direction": direction,
        "message_id": message_id,
        "parsed": parsed,
        "classification": classification,
        "triage": triage,
        "saved_attachments": saved_attachments,
        "fallback_key": fallback_key,
        "conversation_key": conversation_key,
        "thread_id": thread_id,
        "normalized_subject": normalized_subject,
        "conversation_status": conversation_status,
        "processing_errors": processing_errors,
    }


def _prepare_operations_email_records(message: dict) -> list[dict]:
    """Returns one prepared record per detected order block (see
    services.email_parser.detect_order_blocks), or a single-element list -
    today's unchanged behavior - when the message has no multi-order
    split. Each record is produced by the existing, unmodified
    _prepare_operations_email_record(), called once per block; the only
    difference per call is the "body" (and, for block 1+, "attachments")
    field of the message dict it's given."""
    raw_body = safe_str(message.get("body"))
    blocks = detect_order_blocks(raw_body)
    if not blocks:
        return [_prepare_operations_email_record(message)]

    records = []
    for index, block_text in enumerate(blocks):
        block_message = dict(message)
        block_message["body"] = block_text
        if index > 0:
            block_message["attachments"] = []
        record = _prepare_operations_email_record(block_message)
        record["_order_block_index"] = index
        record["_order_block_count"] = len(blocks)
        records.append(record)
    return records


def _insert_operations_email_record_row(message: dict, record: dict) -> dict:
    subject = record["subject"]
    sender = record["sender"]
    received_at = record["received_at"]
    latest_body = record["latest_body"]
    direction = record["direction"]
    message_id = record["message_id"]
    parsed = record["parsed"]
    classification = record["classification"]
    triage = record["triage"]
    saved_attachments = record["saved_attachments"]
    conversation_key = record["conversation_key"]
    thread_id = record["thread_id"]
    normalized_subject = record["normalized_subject"]
    conversation_status = record["conversation_status"]
    source = _email_sync_source_for_direction(direction)

    execute(
        """
        insert into order_intake (
            source,
            source_subject,
            source_sender,
            source_received_at,
            source_message_id,
            email_direction,
            email_mailbox,
            email_thread_id,
            email_normalized_subject,
            email_in_reply_to,
            email_references,
            conversation_status,
            email_synced_at,
            filename,
            file_path,
            parsed_data,
            raw_text,
            intake_status,
            review_status,
            request_type,
            conversation_key,
            matched_load_id,
            confidence_score,
            action_required,
            triage_status,
            triage_engine,
            triage_reason,
            triage_tags,
            triaged_at,
            work_level,
            department_lane,
            work_queue,
            llm_review_required,
            llm_review_reason
        )
        values (
            :source,
            :source_subject,
            :source_sender,
            cast(:source_received_at as timestamptz),
            :source_message_id,
            :email_direction,
            :email_mailbox,
            :email_thread_id,
            :email_normalized_subject,
            :email_in_reply_to,
            cast(:email_references as jsonb),
            :conversation_status,
            now(),
            :filename,
            :file_path,
            cast(:parsed_data as jsonb),
            :raw_text,
            'Synced',
            'Open',
            :request_type,
            :conversation_key,
            :matched_load_id,
            :confidence_score,
            :action_required,
            :triage_status,
            :triage_engine,
            :triage_reason,
            cast(:triage_tags as jsonb),
            now(),
            :work_level,
            :department_lane,
            :work_queue,
            :llm_review_required,
            :llm_review_reason
        )
        """,
        {
            "source": source,
            "source_subject": subject,
            "source_sender": sender,
            "source_received_at": received_at,
            "source_message_id": message_id,
            "email_direction": direction.lower() if direction else "inbound",
            "email_mailbox": safe_str(message.get("mailbox")),
            "email_thread_id": thread_id,
            "email_normalized_subject": normalized_subject,
            "email_in_reply_to": safe_str(message.get("in_reply_to")) or None,
            "email_references": json.dumps(message.get("references") or []),
            "conversation_status": conversation_status,
            "filename": safe_str(saved_attachments[0].get("filename")) if saved_attachments else None,
            "file_path": safe_str(saved_attachments[0].get("file_path")) if saved_attachments else None,
            "parsed_data": json_dump(parsed),
            "raw_text": latest_body,
            "request_type": classification.get("request_type", "Customer Request"),
            "conversation_key": conversation_key,
            "matched_load_id": int_or_none(classification.get("matched_load_id")),
            "confidence_score": int(classification.get("confidence_score", 0) or 0),
            "action_required": classification.get("action_required", "Review imported email."),
            "triage_status": safe_str(triage.get("status")) or "Complete",
            "triage_engine": safe_str(triage.get("engine")) or "fast_rules_v1",
            "triage_reason": safe_str(triage.get("triage_reason")),
            "triage_tags": json.dumps(triage.get("tags") or []),
            "work_level": safe_str(triage.get("work_level")),
            "department_lane": safe_str(triage.get("department_lane")),
            "work_queue": safe_str(triage.get("work_queue")),
            "llm_review_required": bool(triage.get("llm_required") or triage.get("llm_review_required")),
            "llm_review_reason": safe_str(triage.get("llm_reason")) or safe_str(triage.get("llm_review_reason")),
        },
    )

    return {
        "message_id": message_id,
        "conversation_key": conversation_key,
        "thread_id": thread_id,
        "direction": direction.lower() if direction else "inbound",
        "attachments_saved": len(saved_attachments),
        "llm_required": bool(triage.get("llm_required") or triage.get("llm_review_required")),
        "store_only": bool(triage.get("store_only")),
        "work_level": triage.get("work_level"),
        "work_queue": triage.get("work_queue"),
    }


def _insert_operations_email_message(message: dict) -> dict:
    records = _prepare_operations_email_records(message)
    base_message_id = _email_sync_unique_message_id(message)
    records = _assign_split_row_identity(records, base_message_id)

    results = [_insert_operations_email_record_row(message, record) for record in records]

    primary = results[0]
    return {
        **primary,
        "conversation_keys": [result["conversation_key"] for result in results],
        "split_row_count": len(results),
    }


def sync_conversation_status(conversation_key: str) -> None:
    conversation_key = safe_str(conversation_key)
    if not conversation_key:
        return

    try:
        timeline_df = read_df(
            f"""
            select id, coalesce(email_direction, 'inbound') as email_direction
            from order_intake
            where {operations_email_source_filter()}
              and (
                    coalesce(conversation_key, '') = :conversation_key
                 or coalesce(email_thread_id, '') = :conversation_key
                 or coalesce(source_message_id, '') = :conversation_key
              )
            order by coalesce(source_received_at, created_at) desc, id desc
            limit 1
            """,
            {"conversation_key": conversation_key},
        )
    except Exception:
        return

    if timeline_df.empty:
        return

    latest_direction = safe_str(timeline_df.iloc[0].get("email_direction")).lower()
    new_status = "Waiting Customer" if latest_direction == "outbound" else "Customer Replied"

    try:
        execute(
            f"""
            update order_intake
            set conversation_status = :conversation_status
            where {operations_email_source_filter()}
              and (
                    coalesce(conversation_key, '') = :conversation_key
                 or coalesce(email_thread_id, '') = :conversation_key
                 or coalesce(source_message_id, '') = :conversation_key
              )
            """,
            {"conversation_key": conversation_key, "conversation_status": new_status},
        )
    except Exception:
        return


INSERT_LOOP_MIN_SECONDS = 5


def compute_fetch_time_budget(time_budget_seconds, elapsed_before_fetch, insert_reserve_seconds=INSERT_LOOP_MIN_SECONDS):
    """How long fetch_operations_email_sync may run so the insert loop still
    gets its reserved share of the outer sync deadline. ensure_operations_email_sync_schema()
    can cost ~26s on a cold session and fetch itself does several IMAP round
    trips, so elapsed_before_fetch can already exceed the outer budget; this
    always returns at least 5s rather than telling fetch to run for 0 or a
    negative duration."""
    return max(5, time_budget_seconds - elapsed_before_fetch - insert_reserve_seconds)


def compute_insert_loop_deadline(outer_deadline, now, min_seconds=INSERT_LOOP_MIN_SECONDS):
    """The insert loop must always get at least min_seconds to persist
    fetched messages, even if schema-check + fetch already blew past the
    outer deadline - otherwise every sync attempt imports zero messages and
    Last Sync never advances, regardless of what was actually fetched."""
    return max(outer_deadline, now + min_seconds)


def diagnose_operations_email_accounts() -> list[dict]:
    """Test IMAP connection/login/folder-select for every configured
    operations mailbox without importing any email. Safe for a UI button -
    never returns passwords, tokens, or message content."""
    from services import email_client

    return email_client.diagnose_operations_email_accounts()


def sync_operations_email_engine(
    limit: int = 12,
    time_budget_seconds: int = 25,
    *args,
    **kwargs,
) -> dict:
    started = time.perf_counter()

    try:
        time_budget_seconds = max(5, int(time_budget_seconds or 25))
    except Exception:
        time_budget_seconds = 25

    deadline = started + time_budget_seconds

    result = {
        "fetched": 0,
        "inbound_fetched": 0,
        "outbound_fetched": 0,
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "pdf_updated": 0,
        "threads_synced": 0,
        "cases_touched": 0,
        "accounts_synced": 0,
        "triaged": 0,
        "llm_required": 0,
        "store_only": 0,
        "elapsed_seconds": None,
        "stopped_early": False,
        "error": "",
        "error_messages": [],
        "messages": [],
    }

    try:
        ensure_operations_email_sync_schema()
        from services import email_client

        elapsed_before_fetch = time.perf_counter() - started
        fetch_time_budget = compute_fetch_time_budget(time_budget_seconds, elapsed_before_fetch)
        messages = email_client.fetch_operations_email_sync(
            limit=int(limit or 12), time_budget_seconds=fetch_time_budget
        ) or []
        insert_deadline = compute_insert_loop_deadline(deadline, time.perf_counter())
        diagnostics = email_client.get_last_operations_email_sync_diagnostics()
        result["diagnostics"] = diagnostics
        if diagnostics.get("errors") and diagnostics.get("accounts_attempted", 0) == 0:
            result["error"] = "No operations email mailbox could be attempted. Check email addresses and Yahoo app passwords in secrets."
        result["fetched"] = len(messages)
        result["inbound_fetched"] = sum(1 for item in messages if safe_str(item.get("direction")).lower() != "outbound")
        result["outbound_fetched"] = sum(1 for item in messages if safe_str(item.get("direction")).lower() == "outbound")
        result["accounts_synced"] = len({safe_str(item.get("mailbox_account")) for item in messages if safe_str(item.get("mailbox_account"))})

        lookup_limit = max(
            1000,
            min(3000, int(limit or 12) * 40),
        )

        existing_lookup = load_existing_operations_email_lookup(
            limit=lookup_limit
        )
        touched_conversations: set[str] = set()

        for message in messages:
            subject = safe_str(message.get("subject")) or "(no subject)"
            sender = safe_str(message.get("from")) or safe_str(message.get("sender")) or "(unknown sender)"
            received_at = safe_str(message.get("received_at")) or None
            message_id = _email_sync_unique_message_id(message)
            if time.perf_counter() >= insert_deadline:
                result["stopped_early"] = True
                result.setdefault("diagnostics", {})["processing_timed_out"] = True
                result["error_messages"].append(
                    "Email processing stopped after reaching the interactive sync time budget."
                )
                break
            if operations_email_already_imported(
                message_id,
                subject=subject,
                sender=sender,
                received_at=received_at,
                lookup=existing_lookup,
            ):
                result["skipped"] += 1
                continue

            try:
                inserted = _insert_operations_email_message(message)
                result["imported"] += 1
                result["pdf_updated"] += int(inserted.get("attachments_saved", 0) or 0)
                result["triaged"] += 1
                if inserted.get("llm_required"):
                    result["llm_required"] += 1
                if inserted.get("store_only"):
                    result["store_only"] += 1
                touched_conversations.update(
                    key for key in inserted.get("conversation_keys") or [inserted.get("conversation_key")] if key
                )
                result["messages"].append(
                    {
                        "subject": subject,
                        "sender": sender,
                        "direction": inserted.get("direction"),
                        "message_id": message_id,
                    }
                )
                existing_lookup.setdefault("by_message_id", {})[message_id.lower()] = {"source_message_id": message_id}
            except Exception as exc:
                result["errors"] += 1
                result.setdefault("error_messages", []).append(f"{subject[:80]}: {exc}")

        result["threads_synced"] = len(touched_conversations)
        refresh_data()
        for touched_conversation_key in touched_conversations:
            if time.perf_counter() >= deadline:
                result["stopped_early"] = True
                break

            try:
                sync_conversation_status(touched_conversation_key)
                result["threads_synced"] += 1
            except Exception as exc:
                result["errors"] += 1
                result["error_messages"].append(
                    f"Thread status update failed for "
                    f"{touched_conversation_key}: {exc}"
                )
    except Exception as exc:
        result["error"] = str(exc)

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return result


# Compatibility aliases for the standalone operations_email_sync_service wrapper.
_sync_conversation_status = sync_conversation_status
_find_existing_operations_email_record = find_existing_operations_email_record
_load_existing_operations_email_lookup = load_existing_operations_email_lookup
_operations_email_already_imported = operations_email_already_imported
_sync_operations_email_engine = sync_operations_email_engine


def render_operations_case_summary_header(*args, **kwargs) -> None:
    st.caption("Operations case summary header will be moved into a UI component.")


def render_operations_case_panel(*args, **kwargs) -> None:
    st.info("Operations case panel has not been moved from app.py yet.")


def render_operations_attachment_preview(
    *,
    selected_id: int,
    record=None,
    parsed: dict | None = None,
    timing=None,
) -> None:
    """Render saved source documents without recovery or parsing controls."""
    parsed = coerce_json_dict(parsed or {})
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})

    try:
        intake_id = int(selected_id)
    except (TypeError, ValueError):
        st.info("No source documents are available for this work item.")
        return

    metadata_stage = (
        timing.stage("attachment_metadata_loading")
        if timing is not None
        else nullcontext()
    )
    with metadata_stage:
        try:
            conversation_timeline = load_operations_business_conversation_timeline(
                conversation_key=safe_str(record_dict.get("conversation_key")),
                booking_number=safe_str(parsed.get("Booking Number")),
                container_number=safe_str(parsed.get("Container Number")),
                reference_number=safe_str(parsed.get("Reference Number")),
                limit=50,
            )
        except Exception:
            conversation_timeline = pd.DataFrame()
        document_groups = group_operations_source_documents(
            record_dict,
            parsed,
            conversation_timeline,
        )

    st.markdown("##### Source Documents")
    current_documents = document_groups["current"]
    prior_documents = document_groups["prior"]
    record_identity = " ".join(
        [
            safe_str(record_dict.get("request_type")),
            safe_str(record_dict.get("request_type_clean")),
            safe_str(record_dict.get("work_queue")),
            safe_str(record_dict.get("dispatcher_queue")),
        ]
    ).lower()
    is_update = "update" in record_identity or "existing load" in record_identity

    if not current_documents and not prior_documents and not is_update:
        st.info("No source documents are available for this work item.")
        return

    def render_document_group(title: str, documents: list[dict], group_key: str) -> None:
        st.markdown(f"**{title}**")
        if not documents:
            if group_key == "current" and is_update:
                st.info("No new attachment was received with this update.")
            elif group_key == "prior":
                st.caption("No earlier load or conversation documents are available.")
            return

        for index, attachment in enumerate(documents):
            filename = (
                safe_str(attachment.get("original_filename"))
                or safe_str(attachment.get("filename"))
                or f"Attachment {index + 1}"
            )
            received = safe_str(attachment.get("source_received_at"))
            source_subject = safe_str(attachment.get("source_subject"))
            source_details = " · ".join(
                value for value in [received, source_subject] if value
            )
            if source_details:
                st.caption(source_details)

            document_expander = st.expander(
                f"Open / Download Original — {filename}",
                key=f"source_document_{intake_id}_{group_key}_{index}",
                on_change="rerun",
            )
            if not document_expander.open:
                continue

            with document_expander:
                file_path = safe_str(attachment.get("file_path"))
                content_type = (
                    safe_str(attachment.get("content_type"))
                    or "application/octet-stream"
                )
                if not file_path:
                    st.info("The original document file is not available in attachment storage.")
                    continue
                try:
                    file_stage = (
                        timing.stage("attachment_file_access")
                        if timing is not None
                        else nullcontext()
                    )
                    with file_stage:
                        file_bytes = read_operations_attachment_bytes(file_path)
                except Exception:
                    st.warning("The original document could not be opened from attachment storage.")
                    continue

                st.download_button(
                    "Download Original",
                    data=file_bytes,
                    file_name=filename,
                    mime=content_type,
                    key=f"download_original_attachment_{intake_id}_{group_key}_{index}",
                    width="stretch",
                )
                is_pdf = (
                    is_pdf_filename(filename, content_type)
                    or bool(attachment.get("is_pdf"))
                )
                if is_pdf and render_pdf_preview is not None:
                    pdf_stage = (
                        timing.stage("pdf_preparation_and_render")
                        if timing is not None
                        else nullcontext()
                    )
                    with pdf_stage:
                        render_pdf_preview(
                            file_bytes,
                            key=f"source_pdf_preview_{intake_id}_{group_key}_{index}",
                        )
                elif not is_pdf:
                    st.caption("Use Download Original to open this source document.")

    render_document_group("Attached to This Message", current_documents, "current")
    if is_update or prior_documents:
        render_document_group(
            "Existing Load / Conversation Documents",
            prior_documents,
            "prior",
        )


def render_operations_pdf_panel(
    *,
    selected_id: int,
    record=None,
    parsed: dict | None = None,
    subject: str = "",
    sender: str = "",
    body: str = "",
    matched_load_id=None,
    conversation_key: str = "",
) -> None:
    """
    Admin attachment/PDF recovery and parsing panel for Operations Inbox.

    This replaces the old placeholder and keeps all document actions close to
    the Operations Inbox service:
    - show saved attachments
    - allow manual attachment upload
    - parse/reparse saved files
    - compare email body fields to document fields
    - save approved document fields back to order_intake.parsed_data
    - attach document to a matched load
    - update or create loads from approved document fields
    """
    parsed = coerce_json_dict(parsed or {})
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})

    try:
        intake_id = int(selected_id)
    except Exception:
        st.warning("Unable to load attachment panel because the intake id is invalid.")
        return

    attachments = extract_operations_attachments(parsed, record_dict)
    pdf_attachments = extract_operations_pdf_attachments(parsed, record_dict)

    expanded = bool(attachments or pdf_attachments)
    with st.expander("Attachment recovery, parsing, and reconciliation", expanded=expanded):
        st.caption("Review saved email attachments, parse PDFs/documents, approve fields, and attach or apply them to loads.")

        rescan_cols = st.columns([3, 1])
        with rescan_cols[1]:
            if st.button(
                "Check Source Email for Attachments",
                key=f"ops_rescan_attachments_{intake_id}",
                use_container_width=True,
            ):
                from services import email_client

                with st.spinner("Checking the source mailbox for attachments..."):
                    rescan_result = rescan_operations_request_attachments(
                        record_dict,
                        fetch_by_message_id=email_client.fetch_operations_email_by_message_id,
                        fetch_recent_emails=lambda limit: email_client.fetch_operations_email_near_date(
                            sender=safe_str(record_dict.get("source_sender", "")),
                            received_at=record_dict.get("source_received_at"),
                            limit=min(limit, 20),
                        ),
                    )

                if rescan_result.get("saved"):
                    st.success(
                        f"Found and saved {rescan_result['saved']} attachment(s): "
                        + ", ".join(rescan_result.get("attachment_names", []))
                    )
                    refresh_data()
                    st.rerun()
                elif rescan_result.get("matched"):
                    st.info("Matched the source email, but it had no new attachments to save.")
                else:
                    st.warning(
                        "Could not find a matching source email with attachments. "
                        "The message may no longer be available in the configured mailbox, "
                        "or it may not have contained a supported attachment. "
                        "You can also upload the file manually below."
                    )

        uploaded_files = st.file_uploader(
            "Manually Add Missing Attachment",
            type=["pdf", "docx", "txt", "csv"],
            accept_multiple_files=True,
            key=f"ops_attachment_upload_{intake_id}",
        )

        if uploaded_files:
            if st.button("Import Uploaded Attachment(s)", key=f"import_uploaded_ops_attachments_{intake_id}"):
                imported = []
                errors = []
                existing = extract_operations_attachments(parsed, record_dict)
                for index, uploaded_file in enumerate(uploaded_files, start=len(existing) + 1):
                    try:
                        saved = import_uploaded_operations_attachment(
                            uploaded_file=uploaded_file,
                            message_id=f"intake-{intake_id}",
                            attachment_index=index,
                        )
                        imported.append(saved)
                    except Exception as exc:
                        errors.append(f"{getattr(uploaded_file, 'name', 'attachment')}: {exc}")

                if imported:
                    updated_parsed = merge_saved_attachment_fields(parsed, existing + imported)
                    store_operations_parsed_data(
                        intake_id,
                        updated_parsed,
                        action_required=order_action_required_from_parsed(updated_parsed),
                    )
                    st.success(f"Imported {len(imported)} attachment(s).")
                    refresh_data()
                    st.rerun()

                if errors:
                    st.error("; ".join(errors))

        attachments = extract_operations_attachments(parsed, record_dict)
        if not attachments:
            st.info("No saved attachments are available for this operations request yet.")
            return

        labels = []
        for index, attachment in enumerate(attachments, start=1):
            filename = safe_str(attachment.get("filename", "Attachment")) or f"Attachment {index}"
            file_type = "PDF" if is_pdf_filename(filename, attachment.get("content_type", "")) or attachment.get("is_pdf") else "File"
            fields_found = attachment.get("fields_found", "")
            label = f"{index}. {filename} ({file_type}"
            if safe_str(fields_found):
                label += f", {fields_found} fields"
            label += ")"
            labels.append(label)

        selected_label = st.selectbox(
            "Saved attachment",
            labels,
            key=f"operations_attachment_selection_{intake_id}",
        )
        selected_index = labels.index(selected_label)
        selected_attachment = attachments[selected_index]

        filename = safe_str(selected_attachment.get("filename", "attachment")) or "attachment"
        file_path = safe_str(selected_attachment.get("file_path", ""))
        content_type = safe_str(selected_attachment.get("content_type", "application/octet-stream")) or "application/octet-stream"

        c1, c2, c3 = st.columns(3)
        c1.metric("Attachment", filename)
        c2.metric("Type", "PDF" if is_pdf_filename(filename, content_type) or selected_attachment.get("is_pdf") else "File")
        c3.metric("Fields Found", safe_str(selected_attachment.get("fields_found", "")) or "0")

        if safe_str(selected_attachment.get("parse_error", "")):
            st.warning(f"Parse warning: {selected_attachment.get('parse_error')}")

        if file_path:
            try:
                file_bytes = read_operations_attachment_bytes(file_path)
                st.download_button(
                    "Download Attachment",
                    data=file_bytes,
                    file_name=filename,
                    mime=content_type,
                    key=f"download_ops_attachment_{intake_id}_{selected_index}",
                    use_container_width=True,
                )
                if (is_pdf_filename(filename, content_type) or selected_attachment.get("is_pdf")) and render_pdf_preview is not None:
                    render_pdf_preview(file_bytes, key=f"ops_pdf_preview_{intake_id}_{selected_index}")
            except Exception as exc:
                st.warning(f"Attachment file could not be read from storage: {exc}")

        document_parsed = coerce_json_dict(selected_attachment.get("parsed_data", {}))

        action_cols = st.columns(4)
        with action_cols[0]:
            if st.button(
                "Parse / Reparse / Repair",
                key=f"parse_ops_attachment_{intake_id}_{selected_index}",
                use_container_width=True,
            ):
                if not file_path:
                    st.error("This attachment has no saved file path.")
                else:
                    try:
                        text_preview, reparsed = parse_saved_operations_attachment(
                            file_path,
                            filename,
                            content_type,
                        )
                        updated_attachment = dict(selected_attachment)
                        updated_attachment["parsed_data"] = reparsed
                        updated_attachment["fields_found"] = field_count(reparsed)
                        updated_attachment["text_preview"] = text_preview[:1800]
                        updated_attachment["parse_error"] = ""

                        updated_attachments = list(attachments)
                        updated_attachments[selected_index] = updated_attachment
                        updated_parsed = merge_saved_attachment_fields(parsed, updated_attachments, force=True)
                        store_operations_parsed_data(
                            intake_id,
                            updated_parsed,
                            action_required=order_action_required_from_parsed(updated_parsed),
                        )
                        st.success("Attachment parsed and saved to this request.")
                        refresh_data()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Unable to parse attachment: {exc}")

        if document_parsed and render_document_review_panel is not None:
            review_result = render_document_review_panel(
                intake_id=intake_id,
                parsed=parsed,
                attachment=selected_attachment,
                email_parsed=coerce_json_dict(parsed.get("_email_parsed")) or parsed,
                document_parsed=document_parsed,
                expanded=True,
                allow_edit=True,
            )

            if review_result.get("action") == "save":
                final_values = coerce_json_dict(review_result.get("final_values", {}))
                updated_parsed = dict(parsed)
                for key, value in final_values.items():
                    if not str(key).startswith("_"):
                        updated_parsed[key] = value
                updated_parsed["_document_review"] = {
                    "status": "approved",
                    "filename": filename,
                    "conflicts": review_result.get("conflicts", []),
                }
                store_operations_parsed_data(
                    intake_id,
                    updated_parsed,
                    action_required="Document fields approved by dispatcher.",
                )
                st.success("Approved document fields saved to this operations request.")
                refresh_data()
                st.rerun()

            elif review_result.get("action") == "needs_review":
                updated_parsed = dict(parsed)
                updated_parsed["_document_review"] = {
                    "status": "needs_review",
                    "filename": filename,
                    "conflicts": review_result.get("conflicts", []),
                }
                store_operations_parsed_data(
                    intake_id,
                    updated_parsed,
                    action_required="Document needs dispatcher review.",
                )
                st.success("Document marked as needs review.")
                refresh_data()
                st.rerun()

            elif review_result.get("action") == "reject":
                updated_parsed = dict(parsed)
                updated_parsed["_document_review"] = {
                    "status": "rejected",
                    "filename": filename,
                }
                store_operations_parsed_data(
                    intake_id,
                    updated_parsed,
                    action_required="Document parse rejected by dispatcher.",
                )
                st.success("Document parse marked rejected.")
                refresh_data()
                st.rerun()

        elif document_parsed:
            rows, conflicts, final_values = merge_operations_order_fields(parsed, document_parsed)
            if conflicts:
                st.warning("Review mismatches: " + ", ".join(conflicts))
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("This attachment has not produced parsed document fields yet. Use Parse / Reparse above.")

        with st.expander("Parser metadata and raw output", expanded=False):
            parser_details = coerce_json_dict(
                document_parsed.get("_hybrid_document_parser")
                if isinstance(document_parsed, dict)
                else {}
            )
            st.write(
                f"**Parser:** "
                f"{safe_str(parser_details.get('parser_name')) or safe_str(selected_attachment.get('parser_name')) or 'Stored document parser'}"
            )
            st.write(
                f"**Parse status:** "
                f"{'Error' if safe_str(selected_attachment.get('parse_error')) else 'Complete' if document_parsed else 'Not parsed'}"
            )
            st.write(
                f"**Last parsed/imported:** "
                f"{safe_str(selected_attachment.get('parsed_at')) or safe_str(selected_attachment.get('imported_at')) or '-'}"
            )
            st.write(
                f"**Extraction confidence:** "
                f"{safe_str(document_parsed.get('_confidence')) if isinstance(document_parsed, dict) else '-'}"
            )
            st.write(
                f"**Storage identifier:** "
                f"{Path(file_path).name if file_path else '-'}"
            )
            st.markdown("**Raw extracted fields**")
            st.json(document_parsed or {})
            st.markdown("**Conflict and reconciliation details**")
            st.json(
                {
                    "reconciliation": parsed.get("_reconciliation") or {},
                    "document_review": parsed.get("_document_review") or {},
                    "processing_errors": parsed.get("_parser_failures") or [],
                }
            )

        document_fields = document_parsed or coerce_json_dict(selected_attachment.get("parsed_data", {}))

        load_cols = st.columns(3)
        with load_cols[0]:
            if matched_load_id and st.button("Attach to Matched Load", key=f"attach_ops_attachment_{intake_id}_{selected_index}", use_container_width=True):
                result = attach_saved_operations_file_to_load(
                    load_id=matched_load_id,
                    attachment=selected_attachment,
                    document_type="Operations PDF" if is_pdf_filename(filename, content_type) else "Operations Attachment",
                )
                if result.get("attached"):
                    st.success("Attachment linked to matched load.")
                    refresh_data()
                    st.rerun()
                else:
                    st.error(result.get("error") or "Attachment could not be linked to the load.")

        with load_cols[1]:
            if matched_load_id and st.button("Update Matched Load", key=f"update_load_from_ops_attachment_{intake_id}_{selected_index}", use_container_width=True):
                result = update_load_from_operations_pdf(
                    matched_load_id,
                    document_fields,
                    overwrite=False,
                )
                if result.get("updated_fields"):
                    st.success("Updated load fields: " + ", ".join(result.get("updated_fields", [])))
                    refresh_data()
                    st.rerun()
                else:
                    st.info(result.get("error") or "No load fields needed updating.")

        with load_cols[2]:
            if st.button("Create Load From Document", key=f"create_load_from_ops_attachment_{intake_id}_{selected_index}", use_container_width=True):
                try:
                    merged_for_load = dict(parsed)
                    for key, value in document_fields.items():
                        if not str(key).startswith("_") and safe_str(value):
                            merged_for_load[key] = value
                    new_load_id = create_load_from_intake(intake_id, merged_for_load)
                    st.success(f"Created load #{new_load_id} from document fields.")
                    refresh_data()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Unable to create load from document fields: {exc}")


# Compatibility aliases for page code that still uses old names
_sync_operations_email_engine = sync_operations_email_engine
_render_operations_case_summary_header = render_operations_case_summary_header
_render_operations_case_panel = render_operations_case_panel
_render_operations_attachment_preview = render_operations_attachment_preview
_render_operations_pdf_panel = render_operations_pdf_panel
_render_ops_caption = render_ops_caption
_render_ops_metric_card = render_ops_metric_card

# ============================================================
# Operations Inbox compatibility hotfixes
# ============================================================
# These names are still referenced by pages_app/operations_inbox.py while the
# Operations Inbox page is being split into smaller services. Keep them here so
# the page can import cleanly during the staged refactor.

app_helpers_safe_str = safe_str

try:
    from services.load_matching_service import _candidate_summary_from_context as _candidate_summary_from_context
except Exception:
    def _candidate_summary_from_context(context: dict) -> dict:
        return dict(context or {})

_build_ai_load_context = build_ai_load_context
_valid_ai_suggested_load_id = valid_ai_suggested_load_id


def _conversation_key_from_candidate(candidate: dict, fallback: str = "") -> str:
    candidate = candidate or {}
    for key in [
        "Booking Number",
        "booking_number",
        "Container Number",
        "container_number",
        "Reference Number",
        "reference_number",
        "External Load ID",
        "external_load_id",
        "Load ID",
        "id",
    ]:
        value = safe_str(candidate.get(key, ""))
        if value:
            return value
    return safe_str(fallback)


def _extract_email_address(value: str) -> str:
    try:
        from email.utils import parseaddr
        _, address = parseaddr(str(value or ""))
        return safe_str(address)
    except Exception:
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(value or ""), re.I)
        return match.group(0) if match else safe_str(value)


def _case_customer_from_sender(sender: str) -> str:
    try:
        from email.utils import parseaddr
        name, email = parseaddr(str(sender or ""))
        return safe_str(name) or safe_str(email) or safe_str(sender)
    except Exception:
        return safe_str(sender)


def _operations_ai_rule_context(classification: dict, parsed: dict, subject: str, body: str) -> dict:
    classification = classification or {}
    parsed = parsed or {}
    tokens = classification.get("tokens") or extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    return {
        "request_type": classification.get("request_type", "Customer Request"),
        "confidence_score": classification.get("confidence_score", 0),
        "action_required": classification.get("action_required", ""),
        "conversation_key": classification.get("conversation_key", ""),
        "matched_load_id": classification.get("matched_load_id"),
        "tokens": tokens,
        "intent_scores": classification.get("intent_scores", {}),
        "load_match_candidates": classification.get("load_match_candidates", []),
    }


try:
    from config import get_secret as _ops_get_secret
except Exception:
    _ops_get_secret = None


def _get_app_setting(name: str, default=None):
    if _ops_get_secret is None:
        return default
    try:
        return _ops_get_secret(name, default)
    except Exception:
        return default


def _split_email_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _split_email_addresses(value: str) -> list[str]:
    try:
        from email.utils import getaddresses
        return [safe_str(address) for _, address in getaddresses([str(value or "").replace(";", ",")]) if safe_str(address)]
    except Exception:
        return _split_email_list(value)


OPERATIONS_REPLY_MAILBOXES = [
    "dispatch@calitranscorp.com",
    "margiea@calitranscorp.com",
    "accounting@calitranscorp.com",
]

def _extract_booking_from_text(text: str) -> str:
    text = safe_str(text)

    patterns = [
        r"\bBK[-\s]?\d{5,12}\b",
        r"\bBKG[\s#:.-]*([A-Z0-9-]{5,20})\b",
        r"\bBOOKING[\s#:.-]*([A-Z0-9-]{5,20})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1) if match.lastindex else match.group(0)
            return safe_str(value).replace(" ", "").upper()

    return ""


def _extract_container_from_text(text: str) -> str:
    text = safe_str(text)

    match = re.search(r"\b[A-Z]{4}\d{6,7}\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(0).upper()

    return ""
def _operations_reply_sender_options() -> list[str]:
    configured = _split_email_list(
        _get_app_setting(
            "OPERATIONS_REPLY_FROM_ADDRESSES",
            _get_app_setting("OPERATIONS_EMAIL_ACCOUNTS", ",".join(OPERATIONS_REPLY_MAILBOXES)),
        )
    )
    for email_address in OPERATIONS_REPLY_MAILBOXES:
        if email_address not in configured:
            configured.append(email_address)
    seen = set()
    result = []
    for email_address in configured:
        normalized = safe_str(email_address).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(email_address)
    return result
def _first_non_empty(*values) -> str:
    for value in values:
        clean_value = safe_str(value).strip()
        if clean_value:
            return clean_value
    return ""


def _business_conversation_key_from_context(
    *,
    record=None,
    parsed: dict | None = None,
    tokens: dict | None = None,
    subject: str = "",
    body: str = "",
    fallback_key: str = "",
) -> str:
    """
    Returns the business-level conversation key used for operational tracking.

    Priority:
    1. Booking number
    2. Container number
    3. Reference number
    4. Existing conversation key
    """

    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}

    record_booking = ""
    record_container = ""
    record_reference = ""
    record_conversation = ""

    if hasattr(record, "get"):
        record_booking = safe_str(record.get("booking_number", ""))
        record_container = safe_str(record.get("container_number", ""))
        record_reference = safe_str(record.get("reference_number", ""))
        record_conversation = safe_str(record.get("conversation_key", ""))

    booking = _first_non_empty(
        tokens.get("booking_number"),
        parsed.get("Booking Number"),
        parsed.get("booking_number"),
        record_booking,
        _extract_booking_from_text(f"{subject}\n{body}"),
    )

    container = _first_non_empty(
        tokens.get("container_number"),
        parsed.get("Container Number"),
        parsed.get("container_number"),
        record_container,
        _extract_container_from_text(f"{subject}\n{body}"),
    )

    reference = _first_non_empty(
        tokens.get("reference_number"),
        parsed.get("Reference Number"),
        parsed.get("reference_number"),
        record_reference,
    )

    return _first_non_empty(
        booking,
        container,
        reference,
        record_conversation,
        fallback_key,
    )


def _get_business_conversation_history(
    *,
    business_key: str,
    booking_number: str = "",
    container_number: str = "",
    reference_number: str = "",
):
    """
    Pull all inbound/outbound messages for the same booking/container/reference,
    even if the raw IMAP/Gmail thread key differs.
    """

    business_key = safe_str(business_key).strip()
    booking_number = safe_str(booking_number).strip()
    container_number = safe_str(container_number).strip()
    reference_number = safe_str(reference_number).strip()

    search_terms = [
        value
        for value in [business_key, booking_number, container_number, reference_number]
        if value
    ]

    if not search_terms:
        return read_df(
            """
            select
                id,
                source_received_at as time,
                email_direction as direction,
                conversation_status,
                review_status,
                source_sender,
                source_subject,
                raw_text
            from order_intake
            where 1 = 0
            """
        )

    clauses = []
    params = {}

    for index, term in enumerate(search_terms):
        param_key = f"term_{index}"
        like_key = f"like_{index}"

        params[param_key] = term
        params[like_key] = f"%{term}%"

        clauses.append(
            f"""
            conversation_key = :{param_key}
            or email_thread_id = :{param_key}
            or email_in_reply_to = :{param_key}
            or source_message_id = :{param_key}
            or source_subject ilike :{like_key}
            or raw_text ilike :{like_key}
            or parsed_data::text ilike :{like_key}
            """
        )

    where_sql = " or ".join(f"({clause})" for clause in clauses)

    return read_df(
        f"""
        select
            id,
            source_received_at as time,
            coalesce(email_direction, 'inbound') as direction,
            coalesce(conversation_status, '') as conversation_status,
            coalesce(review_status, '') as review_status,
            coalesce(source_sender, '') as source_sender,
            coalesce(source_subject, '') as source_subject,
            coalesce(raw_text, '') as raw_text,
            coalesce(conversation_key, '') as conversation_key
        from order_intake
        where {where_sql}
        order by source_received_at asc, id asc
        """,
        params,
    )


def _sync_current_intake_to_business_conversation(
    *,
    intake_id: int,
    business_key: str,
) -> None:
    """
    Make the selected email row use the business conversation key.
    This lets replies to pending orders group under booking/container.
    """

    business_key = safe_str(business_key).strip()
    if not business_key:
        return

    execute(
        """
        update order_intake
        set conversation_key = :business_key
        where id = :intake_id
        """,
        {
            "business_key": business_key,
            "intake_id": int_or_none(intake_id),
        },
    )

def _suggested_operations_reply_sender(request_type: str, operations_case: dict | None) -> str:
    operations_case = operations_case or {}
    owner = safe_str(operations_case.get("owner", "")).lower()
    priority = safe_str(operations_case.get("priority", "")).lower()
    if safe_str(request_type) == "Billing" or owner == "billing":
        return "accounting@calitranscorp.com"
    if "manager" in owner or priority in {"critical", "urgent"}:
        return "margiea@calitranscorp.com"
    return "dispatch@calitranscorp.com"


def _reply_all_cc_from_record(parsed: dict, reply_from: str, reply_to: str) -> str:
    sync_meta = parsed.get("_email_sync", {}) if isinstance(parsed, dict) else {}
    candidates = []
    candidates.extend(_split_email_addresses(sync_meta.get("to", "")))
    candidates.extend(_split_email_addresses(sync_meta.get("cc", "")))
    excluded = {safe_str(reply_from).lower(), safe_str(reply_to).lower()}
    cleaned = []
    seen = set()
    for address in candidates:
        normalized = safe_str(address).lower()
        if not normalized or normalized in excluded or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(address)
    return ", ".join(cleaned)


def _default_operations_action_subject(subject: str, request_type: str, action_mode: str) -> str:
    clean_subject = safe_str(subject)
    if action_mode == "Forward":
        if clean_subject.lower().startswith(("fw:", "fwd:")):
            return clean_subject
        return f"Fwd: {clean_subject}" if clean_subject else f"Fwd: {request_type}"
    if clean_subject.lower().startswith("re:"):
        return clean_subject
    return f"Re: {clean_subject}" if clean_subject else f"Re: {request_type}"


def _default_operations_reply_body(
    request_type: str,
    parsed: dict,
    matched_load_id,
    subject: str = "",
    body: str = "",
    reply_language: str = "Auto",
    reply_tone: str = "Professional",
) -> str:
    parsed = parsed or {}
    company_name = _get_app_setting("COMPANY_NAME", "CaliTrans")
    tokens = extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    reference = (
        safe_str(parsed.get("Booking Number", ""))
        or safe_str(parsed.get("Container Number", ""))
        or safe_str(parsed.get("Reference Number", ""))
        or safe_str(tokens.get("booking_number", ""))
        or safe_str(tokens.get("container_number", ""))
        or (f"load {matched_load_id}" if matched_load_id else "your request")
    )
    language = resolve_reply_language(reply_language, subject, body)
    english = (
        "Hello,\n\n"
        f"We received your {safe_str(request_type).lower() or 'request'} for {reference}. "
        "Our dispatch team is reviewing it now and will follow up with the next update or any missing details.\n\n"
        f"Thank you,\n{company_name} Dispatch"
    )
    spanish = (
        "Hola,\n\n"
        f"Recibimos su solicitud para {reference}. "
        "Nuestro equipo de despacho la esta revisando y le dara seguimiento con la proxima actualizacion o cualquier informacion necesaria.\n\n"
        f"Gracias,\n{company_name} Dispatch"
    )
    if language == "Spanish":
        return spanish
    if language == "Bilingual":
        return f"{english}\n\n---\n\n{spanish}"
    return english


def _get_operations_case_id(record) -> int | None:
    if record is None or not hasattr(record, "get"):
        return None
    return int_or_none(record.get("case_id"))


def _get_operations_case(record) -> dict:
    case_id = _get_operations_case_id(record)
    if case_id is None:
        return {}
    try:
        return load_operations_case_by_id(case_id) or {}
    except Exception:
        return {}


def _save_operations_email_reply(
    *,
    intake_id: int,
    load_id,
    case_id=None,
    recipient: str,
    subject: str,
    body: str,
    status: str,
    error_message: str = "",
) -> None:
    execute(
        """
        insert into operations_email_replies (
            intake_id, load_id, case_id, recipient, subject, body, status, error_message,
            sent_at, sent_by
        )
        values (
            :intake_id, :load_id, :case_id, :recipient, :subject, :body, :status, :error_message,
            case when :status = 'sent' then now() else null end, 'dispatcher'
        )
        """,
        {
            "intake_id": int_or_none(intake_id),
            "load_id": int_or_none(load_id),
            "case_id": int_or_none(case_id),
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "status": status,
            "error_message": error_message or None,
        },
    )
def _new_outbound_source_message_id(intake_id: int) -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"tms-reply-{intake_id}-{timestamp}-{uuid4().hex[:8]}"
def _insert_operations_thread_reply_record(
    *,
    intake_id: int,
    record,
    reply_from: str,
    reply_to: str,
    reply_subject: str,
    reply_body: str,
    reply_cc: str = "",
    request_type: str,
    conversation_key: str,
    matched_load_id,
    case_id=None,
    create_case_if_missing: bool = False,
) -> None:
    source_message_id = safe_str(record.get("source_message_id", "")) if hasattr(record, "get") else ""
    email_thread_id = safe_str(record.get("email_thread_id", "")) if hasattr(record, "get") else ""
    normalized_subject = safe_str(record.get("email_normalized_subject", "")) if hasattr(record, "get") else ""

    outbound_message_id = _new_outbound_source_message_id(int(intake_id))
    resolved_conversation_key = safe_str(conversation_key) or safe_str(record.get("conversation_key", "")) if hasattr(record, "get") else safe_str(conversation_key)

    execute(
        """
        insert into order_intake (
            source,
            source_subject,
            source_sender,
            source_received_at,
            source_message_id,
            email_direction,
            email_mailbox,
            email_thread_id,
            email_normalized_subject,
            email_in_reply_to,
            email_references,
            conversation_status,
            email_synced_at,
            parsed_data,
            raw_text,
            intake_status,
            review_status,
            request_type,
            conversation_key,
            matched_load_id,
            case_id,
            confidence_score,
            action_required
        )
        values (
            'operations_email_sent',
            :source_subject,
            :source_sender,
            now(),
            :source_message_id,
            'outbound',
            'TMS',
            :email_thread_id,
            :email_normalized_subject,
            :email_in_reply_to,
            cast(:email_references as jsonb),
            'Waiting Customer',
            now(),
            cast(:parsed_data as jsonb),
            :raw_text,
            'Synced',
            'Closed',
            :request_type,
            :conversation_key,
            :matched_load_id,
            :case_id,
            100,
            'Dispatcher reply sent from TMS.'
        )
        """,
        {
            "source_subject": reply_subject,
            "source_sender": reply_from or "TMS",
            "source_message_id": outbound_message_id,
            "email_thread_id": email_thread_id or resolved_conversation_key or source_message_id or outbound_message_id,
            "email_normalized_subject": normalized_subject or safe_str(reply_subject).lower(),
            "email_in_reply_to": source_message_id or None,
            "email_references": json.dumps([value for value in [source_message_id, email_thread_id, resolved_conversation_key] if safe_str(value)]),
            "parsed_data": json_dump(
                {
                    "_email_sync": {
                        "direction": "outbound",
                        "from": reply_from,
                        "to": reply_to,
                        "cc": reply_cc,
                        "source": "tms_reply",
                        "parent_intake_id": int_or_none(intake_id),
                    }
                }
            ),
            "raw_text": reply_body,
            "request_type": request_type,
            "conversation_key": resolved_conversation_key,
            "matched_load_id": int_or_none(matched_load_id),
            "case_id": int_or_none(case_id),
        },
    )

    if resolved_conversation_key:
        try:
            execute(
                """
                update public.order_intake_drafts
                set latest_direction = 'outbound',
                    latest_subject = :latest_subject,
                    latest_sender = :latest_sender,
                    last_dispatch_message_at = now(),
                    draft_status = case
                        when draft_status in ('Ready To Create', 'Needs Details', 'Pending Review')
                        then 'Waiting Customer'
                        else draft_status
                    end
                where conversation_key = :conversation_key
                  and created_load_id is null
                """,
                {
                    "conversation_key": resolved_conversation_key,
                    "latest_subject": reply_subject,
                    "latest_sender": reply_from,
                },
            )
        except Exception:
            pass

    try:
        sync_conversation_status(resolved_conversation_key)
    except Exception:
        pass
def _save_load_communication(
    load_id,
    intake_id,
    conversation_key,
    request_type,
    subject,
    sender,
    body,
    direction: str = "inbound",
    case_id=None,
) -> None:
    execute(
        """
        insert into load_communications (
            load_id, intake_id, case_id, conversation_key, communication_type,
            direction, subject, sender, message_body
        )
        values (
            :load_id, :intake_id, :case_id, :conversation_key, :communication_type,
            :direction, :subject, :sender, :message_body
        )
        """,
        {
            "load_id": int_or_none(load_id),
            "intake_id": int_or_none(intake_id),
            "case_id": int_or_none(case_id),
            "conversation_key": conversation_key,
            "communication_type": request_type,
            "direction": direction,
            "subject": subject,
            "sender": sender,
            "message_body": body,
        },
    )


def _save_operations_ai_feedback(**kwargs) -> None:
    try:
        from services.operational_ai_feedback_service import save_operations_ai_feedback
        save_operations_ai_feedback(**kwargs)
    except Exception:
        # Feedback should never block dispatch workflow.
        pass


try:
    from services.customer_status_email_service import _send_smtp_email as _send_smtp_email
except Exception:
    def _send_smtp_email(*args, **kwargs):
        raise RuntimeError("SMTP email service is not available.")


try:
    from ai_agents.intent_agent import IntentAgent
    intent_agent = IntentAgent()
except Exception:
    intent_agent = None

try:
    from ai_agents.operations_parser_agent import OperationsParserAgent
    operations_parser_agent = OperationsParserAgent()
except Exception:
    operations_parser_agent = None

try:
    from ai_agents.load_intelligence_agent import LoadIntelligenceAgent
    load_intelligence_agent = LoadIntelligenceAgent()
except Exception:
    load_intelligence_agent = None

try:
    from ai_agents.workflow_agent import WorkflowAgent
    workflow_agent = WorkflowAgent()
except Exception:
    workflow_agent = None

try:
    from ai_agents.response_agent import ResponseAgent
    response_agent = ResponseAgent()
except Exception:
    response_agent = None
