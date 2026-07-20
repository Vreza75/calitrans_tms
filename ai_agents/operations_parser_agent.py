# ai_agents/operations_parser_agent.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from ai_core.llm import get_llm


@dataclass
class ParserResult:
    detected_change_type: str
    proposed_updates: Dict
    missing_fields: List[str]
    confidence: float
    requires_human_review: bool
    summary: str
    llm_used: bool
    llm_debug: Dict


class OperationsParserAgent:
    """
    Agent 3: LLM-first Operations Parser.

    Purpose:
    Understand operational meaning from emails:
    appointments, delivery changes, reefer temperature, warehouse changes,
    port changes, missing info, billing docs, POD requests, etc.

    Does NOT update the database directly.
    Only recommends proposed updates.
    """

    def analyze(
        self,
        subject: str,
        body: str,
        intent_result: Optional[Dict] = None,
        existing_load: Optional[Dict] = None,
        company_memory: Optional[Dict] = None,
        conversation_context: Optional[Dict] = None,
    ) -> Dict:

        llm = get_llm()

        system_prompt = """
You are CaliTrans Operations Parser Agent.

CaliTrans is a drayage trucking company handling Port Houston containers,
warehouses, appointments, drivers, billing, and dispatch.

Your job:
Extract operational meaning from the email.

Do NOT invent missing information.
Do NOT update the database.
Only return proposed updates for dispatcher review.

Return only valid JSON.

Allowed detected_change_type examples:
new_booking_details
appointment_request
pickup_appointment_change
delivery_appointment_change
date_change
time_change
warehouse_change
port_change
container_update
booking_update
reefer_temperature
status_request
billing_request
pod_request
cancel_request
missing_information
general_update
unknown

Required JSON keys:
detected_change_type
proposed_updates
missing_fields
confidence
requires_human_review
summary

proposed_updates should use friendly field names such as:
Booking Number
Container Number
Container Qty
Reference Number
Customer
Port
Terminal
Warehouse
Address
Delivery Need Date
Pickup Appointment
Delivery Appointment
Requested Receive Date
Requested Receive Time
Requested Pickup Date
Requested Pickup Time
Reefer Temperature
Commodity
Size
Driver Instructions
Billing Notes
Dispatcher Notes
"""

        user_payload = {
            "email": {
                "subject": subject or "",
                "body": (body or "")[:8000],
            },
            "intent_result": intent_result or {},
            "existing_load": existing_load or {},
            "company_memory": company_memory or {},
            "conversation_context": conversation_context or {},
        }

        llm_result = llm.generate_json(
            task="operations_parser_agent",
            system_prompt=system_prompt,
            user_payload=user_payload,
            temperature=0.1,
        )

        if llm_result.get("ok"):
            data = llm_result.get("output_json", {})

            return asdict(ParserResult(
                detected_change_type=data.get("detected_change_type", "unknown"),
                proposed_updates=data.get("proposed_updates", {}),
                missing_fields=data.get("missing_fields", []),
                confidence=float(data.get("confidence", 0.50)),
                requires_human_review=bool(data.get("requires_human_review", True)),
                summary=data.get("summary", "LLM parser completed."),
                llm_used=True,
                llm_debug=llm_result,
            ))

        return asdict(ParserResult(
            detected_change_type="unknown",
            proposed_updates={},
            missing_fields=[],
            confidence=0.25,
            requires_human_review=True,
            summary="Manual review required. LLM parser failed.",
            llm_used=False,
            llm_debug=llm_result,
        ))