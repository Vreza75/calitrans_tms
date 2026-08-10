# ai_agents/hybrid_email_body_parser.py

from __future__ import annotations

from typing import Dict

from services.email_parser import parse_email_text
from services.operations_field_service import derive_review_state, validate_field_value
from ai_agents.operations_parser_agent import OperationsParserAgent


EMAIL_BODY_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Size",
    "Port",
    "Terminal",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "Pickup Appointment",
    "Delivery Appointment",
    "Requested Receive Date",
    "Requested Receive Time",
    "Requested Pickup Date",
    "Requested Pickup Time",
    "LFD",
    "Document Cutoff",
    "Reefer Temperature",
    "Commodity",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Dispatcher Notes",
]


def _safe_str(value) -> str:
    return str(value or "").strip()


def _field_count(parsed: Dict) -> int:
    return sum(1 for field in EMAIL_BODY_FIELDS if _safe_str(parsed.get(field)))


def _rule_confidence(parsed: Dict) -> float:
    if "_confidence" in parsed:
        return float(parsed.get("_confidence") or 0)
    count = _field_count(parsed)

    if count >= 8:
        return 0.95
    if count >= 6:
        return 0.85
    if count >= 4:
        return 0.70
    if count >= 2:
        return 0.50
    return 0.25


def _merge_rule_and_ai(rule_parsed: Dict, ai_updates: Dict) -> Dict:
    merged = dict(rule_parsed or {})

    for field in EMAIL_BODY_FIELDS:
        rule_value = _safe_str(merged.get(field))
        ai_value = _safe_str(ai_updates.get(field))

        ai_valid = validate_field_value(
            field,
            ai_value,
            source="operations_parser_agent",
            method="ai_suggestion",
        )[0]
        if ai_value and ai_valid and not rule_value:
            merged[field] = ai_value

    current_notes = _safe_str(merged.get("Dispatcher Notes"))
    ai_notes = _safe_str(ai_updates.get("Dispatcher Notes"))

    if ai_notes and ai_notes.lower() not in current_notes.lower():
        merged["Dispatcher Notes"] = f"{current_notes}; {ai_notes}".strip("; ").strip()

    return merged


def parse_email_body_hybrid(
    *,
    subject: str,
    body: str,
    intent_result: Dict | None = None,
    existing_load: Dict | None = None,
    company_memory: Dict | None = None,
    conversation_context: Dict | None = None,
) -> Dict:
    """
    One entry point for parsing email body content.

    1. Run existing fast rule parser.
    2. Score rule result.
    3. If weak, call OperationsParserAgent.
    4. Merge AI proposed updates into parsed fields.
    5. Return one clean result with debug metadata.
    """

    rule_parsed = parse_email_text(subject or "", body or "")
    rule_confidence = _rule_confidence(rule_parsed)
    review = derive_review_state(rule_parsed)

    result = {
        "parser_used": "email_rule_parser",
        "confidence": rule_confidence,
        "needs_review": review["needs_review"],
        "parsed_fields": rule_parsed,
        "rule_parser_output": rule_parsed,
        "operations_parser_agent": {},
        "warnings": [],
    }

    if rule_confidence >= 0.85:
        return result

    parser_agent = OperationsParserAgent()

    ai_result = parser_agent.analyze(
        subject=subject,
        body=body,
        intent_result=intent_result or {},
        existing_load=existing_load or {},
        company_memory=company_memory or {},
        conversation_context=conversation_context or {},
    )

    ai_updates = ai_result.get("proposed_updates", {}) or {}
    ai_confidence = float(ai_result.get("confidence", 0) or 0)

    merged_fields = _merge_rule_and_ai(rule_parsed, ai_updates)

    if ai_result.get("llm_used") and ai_confidence >= rule_confidence:
        result.update({
            "parser_used": "operations_parser_agent",
            "confidence": ai_confidence,
            "needs_review": bool(ai_result.get("requires_human_review", True)),
            "parsed_fields": merged_fields,
            "operations_parser_agent": ai_result,
            "warnings": [],
        })
    else:
        result["operations_parser_agent"] = ai_result
        result["warnings"] = ["Rule parser used because AI parser did not improve confidence."]

    return result
