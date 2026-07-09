# ai_agents/hybrid_document_parser.py

from __future__ import annotations

from typing import Dict

from services.order_parser import parse_order_text
from ai_agents.document_parser_agent import DocumentParserAgent


DOCUMENT_FIELDS = [
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
    return sum(1 for field in DOCUMENT_FIELDS if _safe_str(parsed.get(field)))


def _rule_confidence(parsed: Dict) -> float:
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


def parse_document_hybrid(
    *,
    document_text: str,
    filename: str = "",
    email_subject: str = "",
    email_body: str = "",
    company_memory: Dict | None = None,
) -> Dict:
    """
    One entry point for all document parsing.

    1. Run existing fast regex parser.
    2. Score the result.
    3. If confidence is low, call DocumentParserAgent.
    4. Return one clean parsed result plus debug metadata.
    """

    rule_parsed = parse_order_text(document_text or "")
    rule_confidence = _rule_confidence(rule_parsed)

    result = {
        "parser_used": "rule_parser",
        "confidence": rule_confidence,
        "needs_review": rule_confidence < 0.85,
        "parsed_fields": rule_parsed,
        "rule_parser_output": rule_parsed,
        "document_parser_agent": {},
        "warnings": [],
    }

    if rule_confidence >= 0.85:
        return result

    doc_agent = DocumentParserAgent()

    ai_result = doc_agent.analyze(
        document_text=document_text,
        filename=filename,
        email_subject=email_subject,
        email_body=email_body,
        existing_parsed=rule_parsed,
        company_memory=company_memory or {},
    )

    ai_fields = ai_result.get("parsed_fields", {})
    ai_confidence = float(ai_result.get("confidence", 0) or 0)

    if ai_result.get("llm_used") and ai_confidence >= rule_confidence:
        result.update({
            "parser_used": "document_parser_agent",
            "confidence": ai_confidence,
            "needs_review": bool(ai_result.get("needs_review", True)),
            "parsed_fields": ai_fields,
            "document_parser_agent": ai_result,
            "warnings": ai_result.get("warnings", []),
        })
    else:
        result["document_parser_agent"] = ai_result
        result["warnings"] = ["Rule parser used because AI parser did not improve confidence."]

    return result
