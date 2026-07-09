# operations_email_intake_agent.py

from __future__ import annotations
from ai_core.llm import get_llm

import re
from datetime import datetime
from typing import Any, Dict, List, Optional



INTAKE_MODEL = "gpt-4.1-mini"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def detect_language(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()

    spanish_terms = [
        "hola", "buenos dias", "buenas tardes", "favor", "gracias",
        "carga", "contenedor", "entrega", "recogida", "cotizacion",
        "factura", "pueden", "necesito", "direccion", "camion"
    ]

    spanish_score = sum(1 for term in spanish_terms if term in text)

    if spanish_score >= 2:
        return "Spanish"

    return "English"


def extract_quick_references(subject: str, body: str) -> dict:
    text = f"{subject}\n{body}"

    booking = re.search(
        r"\b(?:booking|bkg|bk|reserva)\s*(?:number|no|#)?\s*[:#-]?\s*([A-Z0-9-]{5,})",
        text,
        re.I,
    )

    container = re.search(r"\b[A-Z]{4}\d{7}\b", text, re.I)

    reference = re.search(
        r"\b(?:ref|reference|po|referencia)\s*(?:number|no|#)?\s*[:#-]?\s*([A-Z0-9-]{4,})",
        text,
        re.I,
    )

    return {
        "booking_number": booking.group(1).upper() if booking else "",
        "container_number": container.group(0).upper() if container else "",
        "reference_number": reference.group(1).upper() if reference else "",
    }


def rule_based_intake(subject: str, body: str, sender: str = "") -> dict:
    text = f"{subject}\n{body}".lower()
    refs = extract_quick_references(subject, body)

    request_type = "Customer Request"
    department = "Dispatch"
    queue = "Action Required"
    priority = "Medium"
    action_required = "Review customer email and determine next action."

    if any(x in text for x in ["quote", "rate request", "pricing", "cotizacion", "cotización", "tarifa"]):
        request_type = "Quote Request"
        queue = "New Orders"
        action_required = "Review quote request and prepare rate."

    elif any(x in text for x in ["new booking", "new order", "delivery order", "work order", "load order"]):
        request_type = "New Booking"
        queue = "New Orders"
        action_required = "Review booking details and create order/load."

    elif any(x in text for x in ["appointment", "appt", "pin", "express pass", "pickup time", "delivery time"]):
        request_type = "Appointment Update"
        queue = "Existing Loads"
        action_required = "Review appointment or PIN update and attach to matching load."

    elif any(x in text for x in ["pod", "proof of delivery", "bol", "documents", "imo", "hazmat"]):
        request_type = "POD / Documents"
        queue = "Documents"
        action_required = "Review attached/requested documents and attach to load."

    elif any(x in text for x in ["invoice", "billing", "payment", "factura", "accessorial", "detention", "demurrage"]):
        request_type = "Billing"
        department = "Accounting"
        queue = "Billing"
        action_required = "Route to Accounting and attach to matching load if available."

    elif any(x in text for x in ["status", "eta", "where is", "update", "estatus", "actualizacion"]):
        request_type = "Status Request"
        queue = "Existing Loads"
        action_required = "Find matching load and prepare status response."

    elif any(x in text for x in ["cancel", "cancelled", "canceled", "cancelar"]):
        request_type = "Cancellation"
        priority = "High"
        queue = "Existing Loads"
        action_required = "Verify matching order before cancelling."

    elif any(x in text for x in ["unsubscribe", "marketing", "promotion", "webinar"]):
        request_type = "Spam / Marketing"
        department = "Archive"
        queue = "Archive"
        priority = "Low"
        action_required = "Archive unless management review is needed."

    if any(x in text for x in ["urgent", "asap", "lfd today", "last free day today", "driver waiting"]):
        priority = "Critical"

    has_reference = any(refs.values())

    return {
        "request_type": request_type,
        "department": department,
        "queue": queue,
        "priority": priority,
        "language": detect_language(subject, body),
        "references": refs,
        "has_reference": has_reference,
        "action_required": action_required,
        "confidence_score": 70 if has_reference else 55,
        "automation_allowed": False,
        "human_review_required": True,
    }


def ai_intake_agent(
    subject: str,
    body: str,
    sender: str = "",
    existing_load_context: Optional[List[dict]] = None,
) -> dict:
    """
    AI Email Intake Agent.
    Classifies incoming transportation emails and recommends workflow routing.
    Human approval should still be required before database updates or replies.
    """

    base = rule_based_intake(subject, body, sender)

    system_prompt = """
You are the CaliTrans AI Email Intake Agent for a drayage/container trucking company.

CaliTrans handles:
- Import
- Export
- Local Import
- Local Export
- Port Houston drayage
- warehouse transfers
- billing/POD requests
- appointment/PIN coordination
- English and Spanish customer emails

Your job:
1. classify the email intent
2. detect language
3. extract operational fields
4. decide department/queue
5. determine if it matches an existing load
6. recommend next action
7. draft a professional response if needed

Never invent missing booking, container, dates, ports, or addresses.
If unsure, mark confidence low and require human review.
Return JSON only.
"""

    user_prompt = {
        "subject": subject,
        "sender": sender,
        "body": body[:8000],
        "rule_based_result": base,
        "existing_load_context": existing_load_context or [],
        "required_json_schema": {
            "request_type": "New Booking | Booking Update | Appointment Update | Status Request | Quote Request | Billing | POD / Documents | Cancellation | Driver Issue | Port Issue | Warehouse Issue | Spam / Marketing | Business Communication | Needs Review",
            "department": "Dispatch | Manager | Accounting | Customer Service | Archive",
            "queue": "Action Required | New Orders | Existing Loads | Waiting | Documents | Billing | Review | Archive",
            "priority": "Critical | High | Medium | Low",
            "language": "English | Spanish | Bilingual",
            "summary": "short summary",
            "extracted_fields": {
                "customer": "",
                "booking_number": "",
                "reference_number": "",
                "container_number": "",
                "order_type": "",
                "port": "",
                "terminal": "",
                "warehouse": "",
                "pickup_address": "",
                "delivery_address": "",
                "pickup_date": "",
                "delivery_date": "",
                "appointment_time": "",
                "lfd": "",
                "cutoff": "",
                "hazmat": "",
                "reefer": "",
                "special_notes": ""
            },
            "match_recommendation": {
                "matched_load_id": "",
                "match_confidence": 0,
                "match_reason": ""
            },
            "recommended_action": "",
            "database_update_recommendation": {
                "create_new_order": False,
                "update_existing_load": False,
                "attach_to_case": False,
                "fields_to_update": {}
            },
            "draft_reply": "",
            "confidence_score": 0,
            "human_review_required": True,
            "automation_allowed": False
        }
    }

    try:
        llm = get_llm()

        llm_result = llm.generate_json(
            task="operations_email_intake_agent",
            system_prompt=system_prompt,
            user_payload=user_prompt,
            temperature=0.1,
        )

        if llm_result.get("ok"):
            result = llm_result.get("output_json", {})

            result["llm_used"] = True
            result["llm_debug"] = {
                "task": llm_result.get("task"),
                "model": llm_result.get("model"),
                "latency_seconds": llm_result.get("latency_seconds"),
                "attempts": llm_result.get("attempts"),
            }

            # Keep fallback values if LLM omitted anything important
            for key, value in base.items():
                result.setdefault(key, value)

        else:
            result = {
                **base,
                "summary": "AI intake failed; using rule-based classification.",
                "error": llm_result.get("error", ""),
                "extracted_fields": {},
                "match_recommendation": {},
                "database_update_recommendation": {},
                "draft_reply": "",
                "llm_used": False,
                "llm_debug": llm_result,
                "human_review_required": True,
                "automation_allowed": False,
            }

    except Exception as exc:
        result = {
            **base,
            "summary": "AI intake failed; using rule-based classification.",
            "error": str(exc),
            "extracted_fields": {},
            "match_recommendation": {},
            "database_update_recommendation": {},
            "draft_reply": "",
            "llm_used": False,
            "llm_debug": {},
            "human_review_required": True,
            "automation_allowed": False,
    }
    result["processed_at"] = datetime.utcnow().isoformat()
    result["agent_name"] = "ai_email_intake_agent_v1"

    return result
