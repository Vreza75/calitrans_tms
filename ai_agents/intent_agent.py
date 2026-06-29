# ai_agents/intent_agent.py

import re
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional


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


class IntentAgent:
    """
    Agent 2: Intent Agent

    Purpose:
    Determine what the customer, driver, warehouse, port, or internal user
    is asking for in English, Spanish, or mixed language.
    """

    INTENTS = {
        "new_booking": [
            "new booking", "new order", "book this", "create load",
            "nueva reserva", "nuevo booking", "nueva orden", "crear carga",
            "favor programar", "please schedule"
        ],
        "status_request": [
            "status", "update", "eta", "where is", "where are we",
            "any update", "track", "tracking",
            "estatus", "estado", "actualización", "actualizacion",
            "dónde está", "donde esta", "eta", "alguna novedad"
        ],
        "appointment_request": [
            "appointment", "appt", "schedule delivery", "pickup appointment",
            "delivery appointment", "set appointment",
            "cita", "programar cita", "hacer cita", "cita de entrega",
            "cita de pickup", "cita de recolección", "cita de recoleccion"
        ],
        "date_change": [
            "change date", "reschedule", "instead of monday", "instead of tuesday",
            "deliver tomorrow", "deliver next week",
            "cambiar fecha", "reprogramar", "en vez de", "mejor mañana",
            "entregar mañana", "cambiar entrega"
        ],
        "warehouse_change": [
            "change warehouse", "new warehouse", "different warehouse",
            "deliver to another location",
            "cambiar warehouse", "cambiar bodega", "otra bodega",
            "nuevo almacén", "nuevo almacen", "otra dirección", "otra direccion"
        ],
        "quote_request": [
            "quote", "rate", "pricing", "how much", "cost",
            "cotización", "cotizacion", "precio", "cuánto cuesta",
            "cuanto cuesta", "tarifa"
        ],
        "billing_docs": [
            "invoice", "billing", "pod", "bol", "proof of delivery",
            "documents", "receipt",
            "factura", "cobro", "documentos", "comprobante",
            "prueba de entrega", "pod", "bol"
        ],
        "cancel_request": [
            "cancel", "void", "do not move", "hold off",
            "cancelar", "cancela", "no mover", "detener", "poner en hold"
        ],
        "driver_eta": [
            "driver eta", "driver location", "where is driver",
            "chofer", "conductor", "ubicación del chofer",
            "ubicacion del chofer", "donde esta el chofer"
        ],
        "spam_or_irrelevant": [
            "unsubscribe", "promotion", "marketing", "seo", "loan offer",
            "viagra", "crypto", "casino"
        ]
    }

    DEPARTMENT_ROUTING = {
        "new_booking": "Dispatch",
        "status_request": "Dispatch",
        "appointment_request": "Dispatch",
        "date_change": "Dispatch",
        "warehouse_change": "Dispatch",
        "quote_request": "Manager",
        "billing_docs": "Accounting",
        "cancel_request": "Manager",
        "driver_eta": "Dispatch",
        "spam_or_irrelevant": "Archive"
    }

    ACTIONS = {
        "new_booking": "Review booking details and create or update load.",
        "status_request": "Check current load status and draft customer update.",
        "appointment_request": "Review pickup/delivery appointment request.",
        "date_change": "Compare requested date change against current load schedule.",
        "warehouse_change": "Review proposed warehouse/location change before updating load.",
        "quote_request": "Prepare rate/quote response or route to manager.",
        "billing_docs": "Review billing/POD/document request and route to accounting.",
        "cancel_request": "Escalate cancellation request before changing load status.",
        "driver_eta": "Check driver/location/ETA and prepare response.",
        "spam_or_irrelevant": "Archive or mark as not operational."
    }

    def analyze(
        self,
        subject: str,
        body: str,
        sender: Optional[str] = None
    ) -> Dict:
        text = f"{subject or ''} {body or ''}".lower()
        text = self._normalize(text)

        language = self._detect_language(text)
        matched_intents = self._match_intents(text)

        if not matched_intents:
            return asdict(IntentResult(
                primary_intent="unknown",
                secondary_intents=[],
                language=language,
                department_owner="Dispatcher Review",
                urgency=self._detect_urgency(text),
                confidence=0.35,
                action_required=True,
                recommended_action="Manual review required. Intent was not clear.",
                reason="No strong operational keywords were found."
            ))

        primary_intent = matched_intents[0]["intent"]
        secondary_intents = [m["intent"] for m in matched_intents[1:]]

        confidence = self._calculate_confidence(matched_intents, text)
        urgency = self._detect_urgency(text)

        return asdict(IntentResult(
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            language=language,
            department_owner=self.DEPARTMENT_ROUTING.get(primary_intent, "Dispatcher Review"),
            urgency=urgency,
            confidence=confidence,
            action_required=primary_intent != "spam_or_irrelevant",
            recommended_action=self.ACTIONS.get(primary_intent, "Review manually."),
            reason=self._build_reason(primary_intent, matched_intents, language)
        ))

    def _normalize(self, text: str) -> str:
        replacements = {
            "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
            "ñ": "n"
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _detect_language(self, text: str) -> str:
        spanish_words = [
            "favor", "cita", "entrega", "recoger", "recoleccion",
            "factura", "cotizacion", "chofer", "bodega",
            "actualizacion", "estado", "cancelar"
        ]

        english_words = [
            "please", "appointment", "delivery", "pickup", "invoice",
            "quote", "status", "update", "driver", "warehouse"
        ]

        spanish_hits = sum(1 for word in spanish_words if word in text)
        english_hits = sum(1 for word in english_words if word in text)

        if spanish_hits > 0 and english_hits > 0:
            return "mixed"
        if spanish_hits > english_hits:
            return "spanish"
        return "english"

    def _match_intents(self, text: str) -> List[Dict]:
        matches = []

        for intent, keywords in self.INTENTS.items():
            score = 0
            found_keywords = []

            for keyword in keywords:
                if keyword in text:
                    score += 1
                    found_keywords.append(keyword)

            if score > 0:
                matches.append({
                    "intent": intent,
                    "score": score,
                    "matched_keywords": found_keywords
                })

        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches

    def _calculate_confidence(self, matches: List[Dict], text: str) -> float:
        top_score = matches[0]["score"]
        total_score = sum(m["score"] for m in matches)

        confidence = 0.50 + min(top_score * 0.12, 0.35)

        if len(matches) == 1:
            confidence += 0.10

        if total_score >= 4:
            confidence += 0.05

        if any(word in text for word in ["booking", "container", "cita", "factura", "quote", "eta"]):
            confidence += 0.05

        return round(min(confidence, 0.98), 2)

    def _detect_urgency(self, text: str) -> str:
        urgent_terms = [
            "urgent", "asap", "immediately", "today", "now",
            "urgente", "hoy", "ahora", "lo antes posible"
        ]

        medium_terms = [
            "tomorrow", "next day", "mañana", "manana", "this week", "esta semana"
        ]

        if any(term in text for term in urgent_terms):
            return "high"

        if any(term in text for term in medium_terms):
            return "medium"

        return "normal"

    def _build_reason(self, primary_intent: str, matches: List[Dict], language: str) -> str:
        keywords = matches[0].get("matched_keywords", [])
        return (
            f"Detected {primary_intent} from {language} request "
            f"based on keywords: {', '.join(keywords[:5])}."
        )