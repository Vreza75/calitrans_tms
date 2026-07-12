# ai_agents/document_parser_agent.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from ai_core.llm import get_llm


@dataclass
class DocumentParserResult:
    parser_used: str
    document_type: str
    vendor_detected: str
    parsed_fields: Dict
    confidence: float
    needs_review: bool
    warnings: List[str]
    summary: str
    llm_used: bool
    llm_debug: Dict


class DocumentParserAgent:
    """
    Smart fallback PDF/document parser.

    Purpose:
    Parse unknown or low-confidence vendor PDF text into standard CaliTrans order fields.

    This agent does NOT create or update loads.
    It only returns proposed parsed fields for dispatcher review.
    """

    STANDARD_FIELDS = [
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

    def analyze(
        self,
        *,
        document_text: str,
        filename: str = "",
        email_subject: str = "",
        email_body: str = "",
        existing_parsed: Optional[Dict] = None,
        company_memory: Optional[Dict] = None,
    ) -> Dict:

        existing_parsed = existing_parsed or {}

        llm = get_llm()

        system_prompt = """
You are CaliTrans Document Parser Agent.

CaliTrans is a small drayage trucking company handling:
- Port Houston
- container imports and exports
- warehouses
- appointments
- reefer loads
- billing documents
- customer work orders

Your job:
Parse raw PDF/document text into standard dispatch/order fields.

Rules:
- Return only valid JSON.
- Do not invent missing information.
- If a value is not clearly present, return an empty string.
- Do not confuse Port and Warehouse.
- Port/Terminal usually means Bayport, Barbours Cut, Port Houston, terminal, ramp, port of loading, port of discharge.
- Warehouse/Destination/Deliver To usually means customer warehouse, shipper, receiver, delivery location, pickup location.
- If fields may be swapped or unclear, add a warning.
- If document is not an order/load document, set needs_review true.

Required JSON keys:
parser_used
document_type
vendor_detected
parsed_fields
confidence
needs_review
warnings
summary

parsed_fields must include these keys when possible:
TYPE
Customer
Booking Number
Reference Number
Container Number
Size
Port
Terminal
Warehouse
Address
Delivery Need Date
Pickup Appointment
Delivery Appointment
LFD
Document Cutoff
Reefer Temperature
Commodity
Contact Name
Contact Email
Contact Phone
Dispatcher Notes
"""

        user_payload = {
            "filename": filename,
            "email_subject": email_subject,
            "email_body": email_body[:3000],
            "existing_rule_parser_output": existing_parsed,
            "document_text": document_text[:12000],
            "company_memory": company_memory or {},
            "standard_fields": self.STANDARD_FIELDS,
        }

        llm_result = llm.generate_json(
            task="document_parser_agent",
            system_prompt=system_prompt,
            user_payload=user_payload,
            temperature=0.1,
        )

        if llm_result.get("ok"):
            data = llm_result.get("output_json", {})
            parsed_fields = data.get("parsed_fields", {}) or {}

            for field in self.STANDARD_FIELDS:
                parsed_fields.setdefault(field, "")

            return asdict(DocumentParserResult(
                parser_used=data.get("parser_used", "llm_document_parser"),
                document_type=data.get("document_type", "unknown"),
                vendor_detected=data.get("vendor_detected", ""),
                parsed_fields=parsed_fields,
                confidence=float(data.get("confidence", 0.50)),
                needs_review=bool(data.get("needs_review", True)),
                warnings=data.get("warnings", []),
                summary=data.get("summary", "Document parsed by LLM fallback parser."),
                llm_used=True,
                llm_debug=llm_result,
            ))

        return asdict(DocumentParserResult(
            parser_used="document_parser_failed",
            document_type="unknown",
            vendor_detected="",
            parsed_fields={field: "" for field in self.STANDARD_FIELDS},
            confidence=0.0,
            needs_review=True,
            warnings=["LLM document parser failed."],
            summary="Document parser failed. Manual review required.",
            llm_used=False,
            llm_debug=llm_result,
        ))