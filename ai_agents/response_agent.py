# ai_agents/response_agent.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional

from ai_core.llm import get_llm


@dataclass
class ResponseResult:
    response_language: str
    response_tone: str
    draft_subject: str
    draft_body: str
    requires_human_review: bool
    reason: str
    llm_used: bool
    llm_debug: Dict


class ResponseAgent:
    """
    Agent 6: Response Agent using centralized LLM wrapper.
    Does not send emails.
    Dispatcher reviews before sending.
    """

    def analyze(
        self,
        subject: str,
        body: str,
        sender: str,
        intent_result: Dict,
        parser_result: Dict,
        load_intelligence_result: Dict,
        workflow_result: Dict,
        existing_load: Optional[Dict] = None,
        company_memory: Optional[Dict] = None,
    ) -> Dict:

        llm = get_llm()

        system_prompt = """
You are CaliTrans Operations Response Agent.

You draft safe, short, professional customer replies for a drayage trucking company.

Rules:
- Return only valid JSON.
- Do not send the email.
- Do not invent ETAs, prices, appointment confirmations, driver locations, or completion status.
- If something is not confirmed, say the operations team is reviewing or checking.
- Match the customer's language: English, Spanish, or bilingual.
- Keep the reply concise and dispatcher-friendly.

Required JSON keys:
response_language
response_tone
draft_subject
draft_body
reason
"""

        user_payload = {
            "company": {
                "name": "CaliTrans",
                "business": "Small drayage trucking company handling Port Houston, containers, warehouses, appointments, billing, and dispatch.",
            },
            "email": {
                "sender": sender,
                "subject": subject,
                "body": body[:6000],
            },
            "agent_context": {
                "intent_result": intent_result,
                "parser_result": parser_result,
                "load_intelligence_result": load_intelligence_result,
                "workflow_result": workflow_result,
                "existing_load": existing_load or {},
                "company_memory": company_memory or {},
            },
        }

        llm_result = llm.generate_json(
            task="response_agent",
            system_prompt=system_prompt,
            user_payload=user_payload,
            temperature=0.2,
        )

        if llm_result.get("ok"):
            data = llm_result.get("output_json", {})

            return asdict(ResponseResult(
                response_language=data.get("response_language", "English"),
                response_tone=data.get("response_tone", "Professional"),
                draft_subject=data.get("draft_subject", self._reply_subject(subject)),
                draft_body=data.get("draft_body", ""),
                requires_human_review=True,
                reason=data.get("reason", "LLM draft created for dispatcher review."),
                llm_used=True,
                llm_debug=llm_result,
            ))

        return self._fallback_response(subject, intent_result, llm_result)

    def _fallback_response(self, subject: str, intent_result: Dict, llm_result: Dict) -> Dict:
        language = str(intent_result.get("language", "english")).lower()

        if language == "spanish":
            body = (
                "Hola,\n\n"
                "Gracias por su mensaje. Nuestro equipo de operaciones está revisando esta solicitud "
                "y le dará seguimiento en breve.\n\n"
                "Gracias,\n"
                "Operaciones CaliTrans"
            )
            response_language = "Spanish"
        else:
            body = (
                "Hello,\n\n"
                "Thank you for your message. Our operations team is reviewing this request "
                "and will follow up shortly.\n\n"
                "Thank you,\n"
                "CaliTrans Operations"
            )
            response_language = "English"

        return asdict(ResponseResult(
            response_language=response_language,
            response_tone="Professional",
            draft_subject=self._reply_subject(subject),
            draft_body=body,
            requires_human_review=True,
            reason=f"Fallback draft used because LLM call failed: {llm_result.get('error', '')}",
            llm_used=False,
            llm_debug=llm_result,
        ))

    def _reply_subject(self, subject: str) -> str:
        subject = str(subject or "").strip()
        if subject.lower().startswith("re:"):
            return subject
        return f"Re: {subject}" if subject else "Re: Shipment Update"