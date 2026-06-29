# ai_agents/operations_parser_agent.py

import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class ParserResult:
    detected_change_type: str
    proposed_updates: Dict
    missing_fields: List[str]
    confidence: float
    requires_human_review: bool
    summary: str


class OperationsParserAgent:
    """
    Agent 3: Operations Parser

    Purpose:
    Understand what operational data changed or is being requested.
    Does NOT update database directly.
    It only recommends proposed updates.
    """

    def analyze(
        self,
        subject: str,
        body: str,
        intent_result: Optional[Dict] = None,
        existing_load: Optional[Dict] = None,
    ) -> Dict:
        text = f"{subject or ''}\n{body or ''}"
        lowered = text.lower()

        proposed_updates = {}

        container = self._extract_container(text)
        booking = self._extract_booking(text)
        date_change = self._extract_date_change(text)
        appointment_time = self._extract_time(text)
        warehouse = self._extract_warehouse(text)
        port = self._extract_port(text)

        if container:
            proposed_updates["Container Number"] = container

        if booking:
            proposed_updates["Booking Number"] = booking

        if date_change:
            proposed_updates["Delivery Need Date"] = date_change

        if appointment_time:
            proposed_updates["Appointment Time"] = appointment_time

        if warehouse:
            proposed_updates["Warehouse"] = warehouse

        if port:
            proposed_updates["Port"] = port

        change_type = self._detect_change_type(lowered, intent_result)

        missing_fields = self._detect_missing_fields(proposed_updates, intent_result)

        confidence = self._calculate_confidence(
            proposed_updates=proposed_updates,
            intent_result=intent_result,
            text=lowered,
        )

        requires_review = confidence < 0.85 or change_type in {
            "date_change",
            "warehouse_change",
            "cancel_request",
        }

        return asdict(ParserResult(
            detected_change_type=change_type,
            proposed_updates=proposed_updates,
            missing_fields=missing_fields,
            confidence=confidence,
            requires_human_review=requires_review,
            summary=self._build_summary(change_type, proposed_updates, confidence),
        ))

    def _extract_container(self, text: str) -> str:
        match = re.search(r"\b[A-Z]{4}\d{7}\b", text, re.I)
        return match.group(0).upper() if match else ""

    def _extract_booking(self, text: str) -> str:
        match = re.search(
            r"\b(?:booking|bkg|bk)\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,})\b",
            text,
            re.I,
        )
        return match.group(1).upper() if match else ""

    def _extract_date_change(self, text: str) -> str:
        patterns = [
            r"\b(?:deliver|delivery|entregar|entrega)\s+(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(?:instead of|en vez de)\s+\w+\s+(?:deliver|delivery|entregar)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1)

        spanish_days = {
            "lunes": "Monday",
            "martes": "Tuesday",
            "miercoles": "Wednesday",
            "miércoles": "Wednesday",
            "jueves": "Thursday",
            "viernes": "Friday",
            "sabado": "Saturday",
            "sábado": "Saturday",
            "domingo": "Sunday",
        }

        lowered = text.lower()
        for spanish, english in spanish_days.items():
            if spanish in lowered:
                return english

        return ""

    def _extract_time(self, text: str) -> str:
        match = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)\b",
            text,
            re.I,
        )
        return match.group(1).strip() if match else ""

    def _extract_warehouse(self, text: str) -> str:
        patterns = [
            r"(?:warehouse|bodega|almacen|almacén)\s*[:#-]?\s*([A-Za-z0-9 ,.-]{4,80})",
            r"(?:deliver to|entregar en)\s+([A-Za-z0-9 ,.-]{4,80})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip()

        return ""

    def _extract_port(self, text: str) -> str:
        ports = [
            "Barbours Cut",
            "Bayport",
            "Port Houston",
            "Houston Terminal",
        ]

        lowered = text.lower()
        for port in ports:
            if port.lower() in lowered:
                return port

        return ""

    def _detect_change_type(self, text: str, intent_result: Optional[Dict]) -> str:
        intent = ""
        if isinstance(intent_result, dict):
            intent = intent_result.get("primary_intent", "")

        if intent:
            return intent

        if any(term in text for term in ["cancel", "cancelar"]):
            return "cancel_request"

        if any(term in text for term in ["reschedule", "change date", "cambiar fecha", "reprogramar"]):
            return "date_change"

        if any(term in text for term in ["warehouse", "bodega", "almacen", "almacén"]):
            return "warehouse_change"

        if any(term in text for term in ["appointment", "appt", "cita"]):
            return "appointment_request"

        return "general_update"

    def _detect_missing_fields(self, proposed_updates: Dict, intent_result: Optional[Dict]) -> List[str]:
        intent = ""
        if isinstance(intent_result, dict):
            intent = intent_result.get("primary_intent", "")

        required_by_intent = {
            "new_booking": ["Booking Number", "Container Number", "Warehouse", "Delivery Need Date"],
            "appointment_request": ["Appointment Time", "Delivery Need Date"],
            "warehouse_change": ["Warehouse"],
            "date_change": ["Delivery Need Date"],
            "status_request": [],
            "billing_docs": [],
        }

        required = required_by_intent.get(intent, [])
        return [field for field in required if not proposed_updates.get(field)]

    def _calculate_confidence(self, proposed_updates: Dict, intent_result: Optional[Dict], text: str) -> float:
        confidence = 0.50

        if proposed_updates:
            confidence += min(len(proposed_updates) * 0.10, 0.30)

        if isinstance(intent_result, dict):
            confidence += float(intent_result.get("confidence", 0)) * 0.20

        if any(term in text for term in ["please", "favor", "attached", "adjunto"]):
            confidence += 0.05

        return round(min(confidence, 0.97), 2)

    def _build_summary(self, change_type: str, proposed_updates: Dict, confidence: float) -> str:
        if not proposed_updates:
            return f"Detected {change_type}, but no direct load field updates were found."

        fields = ", ".join(proposed_updates.keys())
        return f"Detected {change_type}. Proposed update fields: {fields}. Confidence: {confidence}."