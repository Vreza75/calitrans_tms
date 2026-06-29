# ai_agents/workflow_agent.py

from dataclasses import dataclass, asdict
from typing import Dict, Optional


@dataclass
class WorkflowResult:
    workflow_owner: str
    case_status: str
    priority: str
    next_action: str
    customer_waiting: bool
    internal_waiting_on: str
    recommended_queue: str
    requires_human_review: bool
    reason: str


class WorkflowAgent:
    """
    Agent 5: Workflow Agent

    Purpose:
    Decide who owns the work and what should happen next.
    This is workflow routing, not parsing.
    """

    def analyze(
        self,
        intent_result: Dict,
        parser_result: Dict,
        load_intelligence_result: Dict,
        existing_case: Optional[Dict] = None,
    ) -> Dict:

        intent = intent_result.get("primary_intent", "unknown")
        urgency = intent_result.get("urgency", "normal")
        department = intent_result.get("department_owner", "Dispatch")
        load_decision = load_intelligence_result.get("match_decision", "")
        parser_review = parser_result.get("requires_human_review", True)

        priority = self._priority(intent, urgency, load_decision)
        workflow_owner = self._owner(intent, department, load_decision)
        case_status = self._case_status(intent, load_decision)
        next_action = self._next_action(intent, load_decision, parser_result)

        customer_waiting = intent in {
            "status_request",
            "quote_request",
            "driver_eta",
            "billing_docs",
            "appointment_request",
        }

        internal_waiting_on = self._internal_waiting_on(intent, load_decision)

        recommended_queue = self._queue(workflow_owner, case_status, intent)

        requires_review = (
            parser_review
            or load_intelligence_result.get("requires_human_review", True)
            or intent_result.get("confidence", 0) < 0.85
        )

        return asdict(WorkflowResult(
            workflow_owner=workflow_owner,
            case_status=case_status,
            priority=priority,
            next_action=next_action,
            customer_waiting=customer_waiting,
            internal_waiting_on=internal_waiting_on,
            recommended_queue=recommended_queue,
            requires_human_review=requires_review,
            reason=self._reason(intent, workflow_owner, case_status, load_decision),
        ))

    def _priority(self, intent: str, urgency: str, load_decision: str) -> str:
        if urgency == "high":
            return "High"

        if intent in {"cancel_request", "driver_eta"}:
            return "High"

        if load_decision == "needs_load_match_review":
            return "High"

        if intent in {"billing_docs"}:
            return "Medium"

        return "Normal"

    def _owner(self, intent: str, department: str, load_decision: str) -> str:
        if load_decision == "needs_load_match_review":
            return "Dispatch"

        if intent == "quote_request":
            return "Manager"

        if intent == "billing_docs":
            return "Accounting"

        if intent == "cancel_request":
            return "Manager"

        if intent in {
            "status_request",
            "appointment_request",
            "date_change",
            "warehouse_change",
            "driver_eta",
            "new_booking",
        }:
            return "Dispatch"

        if department:
            return department

        return "Dispatch"

    def _case_status(self, intent: str, load_decision: str) -> str:
        if intent == "spam_or_irrelevant":
            return "Closed"

        if load_decision == "needs_load_match_review":
            return "Needs Load Match"

        if load_decision == "create_new_load":
            return "Needs Review"

        if intent in {"status_request", "driver_eta"}:
            return "Needs Response"

        if intent in {"appointment_request", "date_change", "warehouse_change"}:
            return "Needs Dispatcher Review"

        if intent == "billing_docs":
            return "Waiting Billing"

        if intent == "quote_request":
            return "Waiting Manager"

        return "Open"

    def _next_action(self, intent: str, load_decision: str, parser_result: Dict) -> str:
        if load_decision == "needs_load_match_review":
            return "Manually match this message to an existing load or request missing booking/container information."

        if load_decision == "create_new_load":
            return "Review extracted order details and create new load."

        proposed = parser_result.get("proposed_updates", {})

        if intent == "status_request":
            return "Check load status and send customer update."

        if intent == "appointment_request":
            return "Review appointment request and confirm if schedule can support it."

        if intent == "date_change":
            return "Review requested delivery date change before updating load."

        if intent == "warehouse_change":
            return "Review warehouse/location change before updating load."

        if intent == "quote_request":
            return "Manager should prepare quote or approve rate."

        if intent == "billing_docs":
            return "Accounting should review invoice, POD, or billing document request."

        if intent == "cancel_request":
            return "Manager should approve cancellation before changing load status."

        if proposed:
            return "Review proposed load updates before saving."

        return "Review case and determine next operational step."

    def _internal_waiting_on(self, intent: str, load_decision: str) -> str:
        if load_decision == "needs_load_match_review":
            return "Dispatcher"

        if intent == "quote_request":
            return "Manager"

        if intent == "billing_docs":
            return "Accounting"

        if intent == "cancel_request":
            return "Manager"

        return "Dispatch"

    def _queue(self, owner: str, case_status: str, intent: str) -> str:
        if intent == "spam_or_irrelevant":
            return "Archive"

        if owner == "Accounting":
            return "Billing"

        if owner == "Manager":
            return "Manager Review"

        if case_status in {"Needs Response", "Needs Dispatcher Review", "Needs Load Match"}:
            return "Action Required"

        return "Dispatch Review"

    def _reason(self, intent: str, owner: str, status: str, load_decision: str) -> str:
        return (
            f"Intent '{intent}' routed to {owner}. "
            f"Case status set to '{status}' based on load decision '{load_decision}'."
        )