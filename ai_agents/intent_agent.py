# ai_agents/intent_agent.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from ai_core.llm import get_llm


@dataclass
class IntentResult:
    primary_intent: str
    secondary_intents: List[str]
    language: str
    department_owner: str
    urgency: str
    confidence: float
    action_required: bool
    recommended_action: str
    reason: str
    llm_used: bool
    llm_debug: Dict


class IntentAgent:
    """
    Agent 2: LLM-first Intent Agent.
    Determines what the customer is trying to accomplish.
    Supports English, Spanish, and mixed-language emails.
    """

    def analyze(
        self,
        subject: str,
        body: str,
        sender: Optional[str] = None,
        company_memory: Optional[Dict] = None,
        conversation_context: Optional[Dict] = None,
    ) -> Dict:

        llm = get_llm()

        system_prompt = """
You are CaliTrans Intent Agent.

CaliTrans is a small drayage trucking company handling containers, Port Houston, warehouses, appointments, billing, and dispatch.

Your job:
Determine what the sender is trying to accomplish.

Return only valid JSON.

Allowed primary_intent values:
new_booking
booking_update
appointment_request
date_change
warehouse_change
status_request
quote_request
billing_docs
pod_request
cancel_request
driver_eta
port_issue
missing_information
business_communication
spam_or_irrelevant
unknown

Allowed language values:
english
spanish
mixed

Allowed department_owner values:
Dispatch
Manager
Accounting
Archive
Dispatcher Review

Allowed urgency values:
low
normal
medium
high

Required JSON keys:
primary_intent
secondary_intents
language
department_owner
urgency
confidence
action_required
recommended_action
reason
"""

        user_payload = {
            "email": {
                "sender": sender or "",
                "subject": subject or "",
                "body": (body or "")[:6000],
            },
            "company_memory": company_memory or {},
            "conversation_context": conversation_context or {},
        }

        llm_result = llm.generate_json(
            task="intent_agent",
            system_prompt=system_prompt,
            user_payload=user_payload,
            temperature=0.1,
        )

        if llm_result.get("ok"):
            data = llm_result.get("output_json", {})

            return asdict(IntentResult(
                primary_intent=data.get("primary_intent", "unknown"),
                secondary_intents=data.get("secondary_intents", []),
                language=data.get("language", "english"),
                department_owner=data.get("department_owner", "Dispatcher Review"),
                urgency=data.get("urgency", "normal"),
                confidence=float(data.get("confidence", 0.50)),
                action_required=bool(data.get("action_required", True)),
                recommended_action=data.get("recommended_action", "Review manually."),
                reason=data.get("reason", "LLM intent classification completed."),
                llm_used=True,
                llm_debug=llm_result,
            ))

        return asdict(IntentResult(
            primary_intent="unknown",
            secondary_intents=[],
            language="english",
            department_owner="Dispatcher Review",
            urgency="normal",
            confidence=0.25,
            action_required=True,
            recommended_action="Manual review required. LLM intent agent failed.",
            reason=llm_result.get("error", "LLM call failed."),
            llm_used=False,
            llm_debug=llm_result,
        ))