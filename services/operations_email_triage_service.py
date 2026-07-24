# services/operations_email_triage_service.py

from __future__ import annotations

"""Fast, non-LLM Operations Inbox triage.

This module intentionally avoids Streamlit, database calls, and LLM calls. It is
safe to run during email sync for every imported message. The goal is to route
mail quickly and cheaply, then mark only unclear or high-value work for deeper
AI agent review.
"""

from datetime import datetime, timezone
import re
from typing import Any


OPERATIONS_LEVEL = "Level 1 - Operational Cases"
BUSINESS_LEVEL = "Level 2 - Business Communications"
ARCHIVE_LEVEL = "Level 3 - No Action / Archive"
REVIEW_LEVEL = "Needs Review"

KNOWN_REQUEST_TYPES = {
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
}

SPAM_MARKETING_TERMS = [
    "unsubscribe",
    "newsletter",
    "promotion",
    "webinar",
    "seo",
    "lead generation",
    "limited time offer",
    "special offer",
    "advertisement",
    "marketing",
    "sales outreach",
    "sponsored",
]

NO_ACTION_TERMS = [
    "holiday celebration",
    "happy holiday",
    "holiday notice",
    "office closed",
    "dear valued customer",
    "event invitation",
    "invitation to",
    "company announcement",
    "for your records only",
    "no action required",
    "do not reply",
    "system notification",
    "automatic notification",
]

AUTO_REPLY_TERMS = [
    "automatic reply",
    "auto reply",
    "out of office",
    "out-of-office",
    "away from the office",
    "vacation responder",
]

BUSINESS_TERMS = [
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
    "software",
    "it support",
    "password",
    "recruiting",
    "resume",
    "candidate",
    "employment",
    "hr",
    "human resources",
    "credit application",
    "new customer inquiry",
    "sales lead",
]

DOCUMENT_TERMS = [
    "pod",
    "proof of delivery",
    "bol",
    "bill of lading",
    "delivery order",
    "rate confirmation",
    "attached",
    "attachment",
    "document",
    "documents",
]

QUOTE_TERMS = [
    "quote",
    "rate request",
    "please quote",
    "need rate",
    "pricing",
    "cotizacion",
    "cotización",
    "tarifa",
]

NEW_ORDER_TERMS = [
    "new booking",
    "new load",
    "load order",
    "delivery order",
    "work order",
    "tender",
    "please book",
    "please arrange",
    "please schedule",
    "please dispatch",
    "need drayage",
    "nuevo booking",
    "nueva carga",
    "orden de carga",
]

EXISTING_LOAD_TERMS = [
    "appointment",
    "appt",
    "status update",
    "any update",
    "eta",
    "lfd",
    "last free day",
    "container released",
    "released",
    "hold",
    "terminal",
    "gate",
    "pickup",
    "delivery",
    "reschedule",
    "revised",
    "changed",
    "update",
    "actualizacion",
    "actualización",
    "cita",
    "liberado",
]

DRIVER_PORT_TERMS = [
    "driver issue",
    "truck issue",
    "chassis issue",
    "breakdown",
    "flat tire",
    "late driver",
    "accident",
    "no show",
    "port hold",
    "terminal hold",
    "customs hold",
    "line hold",
    "exam",
    "x-ray",
    "trouble ticket",
]

SHIPMENT_TERMS = [
    "booking",
    "container",
    "load",
    "shipment",
    "drayage",
    "pickup",
    "pick up",
    "delivery",
    "deliver",
    "warehouse",
    "terminal",
    "port",
    "vessel",
    "steamship",
    "bol",
    "bill of lading",
    "appointment",
    "lfd",
    "last free day",
    "chassis",
    "booking number",
    "container number",
    "reference number",
]

CONTAINER_RE = re.compile(r"\b[A-Z]{4}\d{6,7}\b", re.I)
BOOKING_RE = re.compile(
    r"\b(?:booking|bkg|bk)\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,})\b",
    re.I,
)
REFERENCE_RE = re.compile(
    r"\b(?:ref(?:erence)?|po|load)\b\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,})\b",
    re.I,
)


def _safe_str(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return text


def _lower_blob(*parts: Any) -> str:
    """Join parts into one lowercase keyword-search blob. A dict part
    contributes its VALUES only, never its Python repr - str({"Port": ""})
    contains the literal word "port" as a dict key even when the field is
    blank, which silently made every message "mention" every parser field
    name (Port, Warehouse, Delivery, Container, ...) regardless of content.
    A list/tuple value (e.g. parsed["Container Numbers"]) is flattened
    element-by-element for the same reason - str(["A", "B"]) is
    "['A', 'B']", not "A B", and would leak Python list syntax into the
    keyword-search text instead of the actual container tokens."""
    flattened: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            for value in part.values():
                if isinstance(value, (list, tuple)):
                    flattened.extend(_safe_str(item) for item in value)
                else:
                    flattened.append(_safe_str(value))
        else:
            flattened.append(_safe_str(part))
    return "\n".join(text for text in flattened if text).lower()


_CONTAINS_ANY_PATTERN_CACHE: dict[str, "re.Pattern"] = {}


def _contains_any(text: str, terms: list[str]) -> bool:
    """Word-boundary substring check - a plain `term in text` let short terms
    like "exam" or "port" false-positive inside unrelated text such as the
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


def _extract_tokens(subject: str, body: str, parsed: dict | None = None) -> dict[str, str]:
    parsed = parsed if isinstance(parsed, dict) else {}
    blob = f"{subject or ''}\n{body or ''}\n{parsed}"

    booking = _safe_str(parsed.get("Booking Number"))
    container = _safe_str(parsed.get("Container Number"))
    reference = _safe_str(parsed.get("Reference Number"))

    if not booking:
        match = BOOKING_RE.search(blob)
        booking = match.group(1).upper() if match else ""
    if not container:
        match = CONTAINER_RE.search(blob)
        container = match.group(0).upper() if match else ""
    if not reference:
        match = REFERENCE_RE.search(blob)
        reference = match.group(1).upper() if match else ""

    return {
        "booking_number": booking,
        "container_number": container,
        "reference_number": reference,
    }


def _has_reference(tokens: dict[str, str]) -> bool:
    return any(_safe_str(tokens.get(key)) for key in ["booking_number", "container_number", "reference_number"])


def _attachment_count(attachments: list[dict] | None, parsed: dict | None = None) -> int:
    count = len(attachments or [])
    parsed = parsed if isinstance(parsed, dict) else {}
    for key in ["_operations_attachments", "_operations_pdf_attachments"]:
        value = parsed.get(key)
        if isinstance(value, list):
            count += len(value)
    return count


def is_booking_confirmation(
    subject: str,
    body: str,
    parsed: dict | None = None,
) -> bool:
    """
    Return True when the message is clearly a booking/order confirmation.

    Booking evidence must take priority over passive invoice language,
    bill-of-lading labels, and rate-sheet terminology. This is the single
    canonical booking-confirmation signal shared by every classification
    entry point (fast triage, insertion, recheck batch, and on-demand
    review) — do not reimplement this check elsewhere.
    """

    parsed = parsed if isinstance(parsed, dict) else {}

    subject_text = _safe_str(subject).lower()
    body_text = _safe_str(body).lower()
    combined = f"{subject_text}\n{body_text}"

    booking_number = _safe_str(
        parsed.get("Booking Number")
        or parsed.get("booking_number")
        or parsed.get("Booking")
    )

    container_qty = _safe_str(
        parsed.get("Container Qty")
        or parsed.get("container_qty")
        or parsed.get("Container Quantity")
        or parsed.get("Number Of Containers")
    )

    container_number = _safe_str(
        parsed.get("Container Number")
        or parsed.get("container_number")
    )

    parse_profile = _safe_str(
        parsed.get("_parse_profile")
        or parsed.get("parse_profile")
    ).lower()

    explicit_subject_signals = [
        "new booking",
        "booking confirmation",
        "new order",
        "load order",
        "delivery order",
        "new import order",
        "new export order",
        "new import booking",
        "new export booking",
    ]

    explicit_body_signals = [
        "booking confirmation",
        "number of cntrs",
        "container qty",
        "containers required",
        "please see the attached booking confirmation",
        "please arrange drayage",
        "please arrange transportation",
    ]

    if any(signal in subject_text for signal in explicit_subject_signals):
        return True

    if any(signal in combined for signal in explicit_body_signals):
        return True

    if parsed.get("_booking_confirmation") is True:
        return True

    if "booking" in parse_profile:
        return True

    if booking_number and (container_qty or container_number):
        return True

    if booking_number and any(
        signal in combined
        for signal in [
            "vessel:",
            "cargo cut off",
            "vgm cut off",
            "port of loading",
            "port of discharge",
            "empty pick up",
            "full return",
        ]
    ):
        return True

    return False


def has_actual_billing_request(
    subject: str,
    body: str,
) -> bool:
    """
    Detect an actionable billing request.

    Passive terms inside a booking confirmation do not make the message Billing.
    Examples excluded:
    - bill of lading
    - charges will be invoiced
    - invoice should be sent to
    - this document is not an invoice
    """

    text = f"{_safe_str(subject)}\n{_safe_str(body)}".lower()

    actual_billing_patterns = [
        r"\bplease\s+(?:send|submit|review|correct|revise|approve)\s+(?:the\s+)?invoice\b",
        r"\bplease\s+invoice\b",
        r"\binvoice\s+(?:request|status|correction|revision|dispute|question|issue)\b",
        r"\bmissing\s+invoice\b",
        r"\bincorrect\s+invoice\b",
        r"\bpayment\s+(?:request|status|due|issue|question)\b",
        r"\bbilling\s+(?:request|question|issue|correction|dispute)\b",
        r"\bdetention\s+(?:invoice|charge|billing)\b",
        r"\bdemurrage\s+(?:invoice|charge|billing)\b",
        r"\baccessorial\s+(?:invoice|charge|billing)\b",
        r"\blumper\s+(?:receipt|invoice|charge)\b",
        r"\bfactura\s+(?:solicitud|correccion|corrección|problema|pendiente)\b",
        r"\bsolicitud\s+de\s+factura\b",
    ]

    return any(
        re.search(pattern, text, flags=re.I)
        for pattern in actual_billing_patterns
    )


def _request_type_from_rules(
    text: str,
    baseline_type: str,
    has_reference: bool,
    attachments_present: bool,
    booking_confirmation: bool = False,
    actual_billing_request: bool = False,
    already_matched_load: bool = False,
) -> str:
    baseline_type = _safe_str(baseline_type)
    if baseline_type not in KNOWN_REQUEST_TYPES:
        baseline_type = "Customer Request"

    if _contains_any(text, SPAM_MARKETING_TERMS):
        return "Spam/Marketing"

    if _contains_any(text, AUTO_REPLY_TERMS):
        return "No Action / FYI"

    if _contains_any(text, NO_ACTION_TERMS) and not has_reference and not _contains_any(text, SHIPMENT_TERMS):
        return "No Action / FYI"

    if _contains_any(text, QUOTE_TERMS) and not booking_confirmation:
        return "Quote Request"

    # A message that already matches a real existing load is an update to
    # that load, never a new booking - even if it also carries strong
    # booking-confirmation signals (e.g. a booking number + container
    # number, which for a *new* booking is exactly the right signal).
    if booking_confirmation and not actual_billing_request and not already_matched_load:
        return "New Booking"

    if actual_billing_request:
        return "Billing"

    if _contains_any(text, NEW_ORDER_TERMS) and (has_reference or attachments_present):
        return "New Booking"

    if _contains_any(text, DOCUMENT_TERMS) and attachments_present:
        if "pod" in text or "proof of delivery" in text:
            return "POD Request"
        return baseline_type if baseline_type in {"New Booking", "Booking Update", "POD Request"} else "Booking Update"

    if _contains_any(text, DRIVER_PORT_TERMS) and has_reference:
        if _contains_any(text, ["driver issue", "truck issue", "chassis issue", "breakdown", "flat tire", "late driver", "accident", "no show"]):
            return "Driver Issue"
        return "Port Issue"

    if _contains_any(text, EXISTING_LOAD_TERMS) and has_reference:
        return "Appointment Update" if _contains_any(text, ["appointment", "appt", "cita", "reschedule"]) else "Booking Update"

    if _contains_any(text, BUSINESS_TERMS) and not has_reference and not _contains_any(text, SHIPMENT_TERMS):
        return "Business Communication"

    return baseline_type


def _work_level_for(request_type: str, text: str, has_reference: bool, confidence: int) -> str:
    if request_type in {"No Action / FYI", "Spam/Marketing"}:
        return ARCHIVE_LEVEL

    if request_type == "Business Communication":
        return BUSINESS_LEVEL

    if request_type == "Billing" and not has_reference:
        return BUSINESS_LEVEL

    if _contains_any(text, BUSINESS_TERMS) and not has_reference and not _contains_any(text, SHIPMENT_TERMS):
        return BUSINESS_LEVEL

    if confidence < 50:
        return REVIEW_LEVEL

    return OPERATIONS_LEVEL


def _department_for(request_type: str, level: str, text: str) -> str:
    if level == ARCHIVE_LEVEL:
        return "Spam" if request_type == "Spam/Marketing" else "Archive / FYI"
    if level == REVIEW_LEVEL:
        return "Human Review"
    if request_type == "Billing":
        return "Accounting"
    if level == BUSINESS_LEVEL:
        if _contains_any(text, ["insurance", "legal", "attorney", "contract", "agreement", "bank", "loan"]):
            return "Management"
        if _contains_any(text, ["recruiting", "resume", "candidate", "employment", "hr", "human resources"]):
            return "Management"
        if _contains_any(text, ["vendor", "supplier", "software", "it support", "password"]):
            return "Management"
        if _contains_any(text, ["sales lead", "new customer inquiry", "credit application"]):
            return "Sales"
        return "Management"
    if request_type in {"Customer Request", "Missing Information"}:
        return "Customer Service"
    return "Dispatch"


def _queue_for(request_type: str, level: str, direction: str, has_reference: bool, attachments_present: bool) -> str:
    if level == ARCHIVE_LEVEL:
        return "Archive"
    if level == BUSINESS_LEVEL:
        return "Billing" if request_type == "Billing" else "Business"
    if level == REVIEW_LEVEL:
        return "Review"
    if direction.lower() == "outbound":
        return "Waiting"
    if request_type == "Quote Request":
        return "Quotes"
    if request_type == "New Booking":
        return "New Orders"
    if request_type in {"POD Request"} or (attachments_present and request_type in {"Booking Update", "Customer Request"}):
        return "Documents"
    if request_type == "Billing":
        return "Billing"
    if request_type in {"Booking Update", "Appointment Update", "Cancellation", "Driver Issue", "Port Issue"} or has_reference:
        return "Existing Loads"
    return "Action Required"


def _llm_need_for(
    *,
    request_type: str,
    level: str,
    confidence: int,
    has_reference: bool,
    attachments_present: bool,
    baseline_type: str,
    text: str,
) -> tuple[bool, str]:
    if level == ARCHIVE_LEVEL:
        return False, "Fast triage determined this is no-action/archive mail. Store it for search, no LLM needed."

    if level == BUSINESS_LEVEL and request_type in {"Business Communication", "Billing"}:
        return False, "Fast triage routed this to a business department. LLM is optional, not required."

    if level == REVIEW_LEVEL:
        return True, "Low confidence or unclear routing. Run AI agents or classify manually."

    if request_type in {"Customer Request", "Missing Information"} and confidence < 80:
        return True, "Customer request is not specific enough for fully automatic routing."

    if request_type in {"New Booking", "Quote Request"}:
        return True, "New order/quote work should get deep AI review before creating a load or quote."

    if request_type in {"Booking Update", "Appointment Update", "Cancellation", "POD Request", "Driver Issue", "Port Issue"} and not has_reference:
        return True, "Operational request does not include a clear booking/container/reference."

    if attachments_present and request_type not in {"No Action / FYI", "Spam/Marketing"}:
        return True, "Attachment/document is present. Run document/AI review before updating a load."

    if confidence < 70:
        return True, "Confidence is below the automatic-routing threshold."

    if baseline_type != request_type and request_type in {"Spam/Marketing", "No Action / FYI", "Business Communication"}:
        return False, "Fast triage overrode the default classifier with a safer storage-only route."

    return False, "Fast triage found enough signals to route without the LLM. Manual review is still available."


def triage_operations_email(
    *,
    sender: str = "",
    subject: str = "",
    body: str = "",
    parsed: dict | None = None,
    attachments: list[dict] | None = None,
    direction: str = "inbound",
    mailbox: str = "",
    classification: dict | None = None,
) -> dict:
    """Return a fast routing decision for an Operations Inbox email.

    This is deterministic/rules-based. It should run during sync and during
    Recheck Groups. It never calls an LLM.
    """

    parsed = parsed if isinstance(parsed, dict) else {}
    classification = classification if isinstance(classification, dict) else {}
    text = _lower_blob(sender, subject, body, parsed)
    tokens = _extract_tokens(subject, body, parsed)
    has_reference = _has_reference(tokens)
    attachments_present = _attachment_count(attachments, parsed) > 0

    baseline_type = _safe_str(classification.get("request_type")) or "Customer Request"
    booking_confirmation = is_booking_confirmation(subject, body, parsed)
    actual_billing_request = has_actual_billing_request(subject, body)
    already_matched_load = classification.get("matched_load_id") is not None
    request_type = _request_type_from_rules(
        text,
        baseline_type,
        has_reference,
        attachments_present,
        booking_confirmation=booking_confirmation,
        actual_billing_request=actual_billing_request,
        already_matched_load=already_matched_load,
    )

    baseline_confidence = classification.get("confidence_score", 0)
    try:
        confidence = int(float(baseline_confidence or 0))
    except Exception:
        confidence = 0

    if request_type in {"Spam/Marketing", "No Action / FYI"}:
        confidence = max(confidence, 90)
    elif request_type == "Business Communication":
        confidence = max(confidence, 82)
    elif has_reference:
        confidence = max(confidence, 78)
    elif request_type in {"Customer Request", "Other"}:
        confidence = max(confidence, 55)
    else:
        confidence = max(confidence, 65)

    level = _work_level_for(request_type, text, has_reference, confidence)
    department = _department_for(request_type, level, text)
    queue = _queue_for(request_type, level, _safe_str(direction) or "inbound", has_reference, attachments_present)
    llm_required, llm_reason = _llm_need_for(
        request_type=request_type,
        level=level,
        confidence=confidence,
        has_reference=has_reference,
        attachments_present=attachments_present,
        baseline_type=baseline_type,
        text=text,
    )

    tags: list[str] = []
    if has_reference:
        tags.append("reference-found")
    if attachments_present:
        tags.append("attachment-present")
    if request_type != baseline_type:
        tags.append("rules-overrode-baseline")
    if level == ARCHIVE_LEVEL:
        tags.append("store-only")
    if _contains_any(text, AUTO_REPLY_TERMS):
        tags.append("auto-reply")
    if _contains_any(text, SPAM_MARKETING_TERMS):
        tags.append("marketing-signal")
    if _contains_any(text, NO_ACTION_TERMS):
        tags.append("no-action-signal")

    if level == ARCHIVE_LEVEL:
        reason = "Stored only: no dispatcher action detected."
    elif level == BUSINESS_LEVEL:
        reason = f"Routed to {department}: business communication, not dispatch work."
    elif level == REVIEW_LEVEL:
        reason = "Needs human review because routing confidence is low."
    else:
        reason = f"Routed to {queue}: {request_type}."

    return {
        "status": "Complete",
        "engine": "fast_rules_v1",
        "triaged_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sender": _safe_str(sender),
        "mailbox": _safe_str(mailbox),
        "direction": (_safe_str(direction) or "inbound").lower(),
        "request_type": request_type,
        "baseline_request_type": baseline_type,
        "work_level": level,
        "department_lane": department,
        "work_queue": queue,
        "confidence_score": confidence,
        "matched_load_id": classification.get("matched_load_id"),
        "conversation_key": _safe_str(classification.get("conversation_key")),
        "tokens": tokens,
        "has_reference": has_reference,
        "attachment_count": _attachment_count(attachments, parsed),
        "llm_required": bool(llm_required),
        "llm_review_required": bool(llm_required),
        "llm_reason": llm_reason,
        "llm_review_reason": llm_reason,
        "store_only": level == ARCHIVE_LEVEL,
        "should_open_case": level == OPERATIONS_LEVEL and request_type not in {"No Action / FYI", "Spam/Marketing"},
        "triage_reason": reason,
        "tags": tags,
    }
