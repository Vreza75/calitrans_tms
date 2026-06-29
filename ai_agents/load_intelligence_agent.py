# ai_agents/load_intelligence_agent.py

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class LoadIntelligenceResult:
    match_decision: str
    matched_load_id: Optional[int]
    confidence: float
    match_reasons: List[str]
    recommended_action: str
    requires_human_review: bool


class LoadIntelligenceAgent:
    """
    Agent 4: Load Intelligence Agent

    Purpose:
    Decide whether an email/update belongs to an existing load,
    an existing case, or should become a new order.
    """

    def analyze(
        self,
        intent_result: Dict,
        parser_result: Dict,
        load_candidates: Optional[List[Dict]] = None,
        conversation_context: Optional[Dict] = None,
    ) -> Dict:

        load_candidates = load_candidates or []
        conversation_context = conversation_context or {}

        reasons = []
        matched_load_id = None
        confidence = 0.0

        # 1. Strongest signal: existing conversation already linked to load
        if conversation_context.get("matched_load_id"):
            matched_load_id = int(conversation_context["matched_load_id"])
            confidence = 0.96
            reasons.append("Existing conversation already linked to load.")

        # 2. Existing load candidate from booking/container/reference match
        elif load_candidates:
            top = load_candidates[0]
            matched_load_id = int(top.get("Load ID"))
            confidence = min(float(top.get("Match Score", 0)) / 100, 0.98)
            reasons.append(top.get("Match Reason", "Matched by load candidate."))

        # 3. No load match, but intent says new booking
        elif intent_result.get("primary_intent") == "new_booking":
            return asdict(LoadIntelligenceResult(
                match_decision="create_new_load",
                matched_load_id=None,
                confidence=0.90,
                match_reasons=["Intent is new booking and no existing load match found."],
                recommended_action="Create new load after dispatcher review.",
                requires_human_review=True,
            ))

        # 4. Status/update request but no match
        elif intent_result.get("primary_intent") in [
            "status_request",
            "date_change",
            "warehouse_change",
            "appointment_request",
            "cancel_request",
            "billing_docs",
            "driver_eta",
        ]:
            return asdict(LoadIntelligenceResult(
                match_decision="needs_load_match_review",
                matched_load_id=None,
                confidence=0.45,
                match_reasons=["Customer appears to reference an existing load, but no load match was found."],
                recommended_action="Dispatcher should manually match this email to a load or request missing reference information.",
                requires_human_review=True,
            ))

        else:
            return asdict(LoadIntelligenceResult(
                match_decision="no_load_action",
                matched_load_id=None,
                confidence=0.50,
                match_reasons=["No clear load relationship detected."],
                recommended_action="Keep in case review unless dispatcher attaches it to a load.",
                requires_human_review=True,
            ))

        requires_review = confidence < 0.90

        return asdict(LoadIntelligenceResult(
            match_decision="attach_to_existing_load",
            matched_load_id=matched_load_id,
            confidence=round(confidence, 2),
            match_reasons=reasons,
            recommended_action="Attach this email/update to the matched load for dispatcher review.",
            requires_human_review=requires_review,
        ))