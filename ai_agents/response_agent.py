# ai_agents/response_agent.py

import json
import streamlit as st
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from openai import OpenAI


@dataclass
class ResponseResult:
    response_language: str
    response_tone: str
    draft_subject: str
    draft_body: str
    requires_human_review: bool
    reason: str
    llm_used: bool


class ResponseAgent:
    """
    Agent 6: LLM Response Agent

    Uses ChatGPT/OpenAI to draft customer replies.
    Does NOT send emails.
    Dispatcher must review before sending.
    """
    def __init__(self, model: str = "gpt-4.1-mini"):
        self.client = OpenAI(
            api_key=st.secrets["OPENAI_API_KEY"]
        )

        self.model = st.secrets.get(
            "OPENAI_RESPONSE_MODEL",
            model
        )

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

        try:
            return self._draft_with_llm(
                subject=subject,
                body=body,
                sender=sender,
                intent_result=intent_result,
                parser_result=parser_result,
                load_intelligence_result=load_intelligence_result,
                workflow_result=workflow_result,
                existing_load=existing_load or {},
                company_memory=company_memory or {},
            )
        except Exception as exc:
            return self._fallback_response(subject, intent_result, workflow_result, str(exc))

    def _draft_with_llm(
        self,
        subject: str,
        body: str,
        sender: str,
        intent_result: Dict,
        parser_result: Dict,
        load_intelligence_result: Dict,
        workflow_result: Dict,
        existing_load: Dict,
        company_memory: Dict,
    ) -> Dict:

        prompt = {
            "company": {
                "name": "CaliTrans",
                "business": "Small drayage trucking company moving containers to and from Port Houston, warehouses, and customers.",
                "office_roles": ["Dispatcher", "Manager", "Accounting"],
                "reply_style": "Professional, clear, friendly, short, operations-focused.",
                "safety_rule": "Never promise completion unless confirmed. Use 'we are reviewing', 'we will confirm', or 'we are checking' when uncertain.",
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
                "existing_load": existing_load,
                "company_memory": company_memory,
            },
            "task": (
                "Draft a customer email reply. Detect whether the reply should be English, Spanish, or bilingual. "
                "Do not send the email. Do not invent appointment times, ETAs, prices, driver locations, or confirmations. "
                "Return only valid JSON with keys: response_language, response_tone, draft_subject, draft_body, reason."
            ),
        }

        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are CaliTrans Operations Response Agent. "
                        "You draft safe, concise customer replies for a drayage dispatcher. "
                        "You must return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, default=str),
                },
            ],
            temperature=0.2,
        )

        raw_text = response.output_text.strip()
        data = json.loads(raw_text)

        return asdict(ResponseResult(
            response_language=data.get("response_language", "English"),
            response_tone=data.get("response_tone", "Professional"),
            draft_subject=data.get("draft_subject", self._reply_subject(subject)),
            draft_body=data.get("draft_body", ""),
            requires_human_review=True,
            reason=data.get("reason", "LLM draft created for dispatcher review."),
            llm_used=True,
        ))

    def _fallback_response(self, subject: str, intent_result: Dict, workflow_result: Dict, error: str) -> Dict:
        language = str(intent_result.get("language", "english")).lower()
        intent = intent_result.get("primary_intent", "unknown")

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
            reason=f"Fallback draft used because LLM call failed: {error}",
            llm_used=False,
        ))

    def _reply_subject(self, subject: str) -> str:
        subject = str(subject or "").strip()
        if subject.lower().startswith("re:"):
            return subject
        return f"Re: {subject}" if subject else "Re: Shipment Update"