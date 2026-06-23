from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return False

try:
    import streamlit as st
except Exception:
    st = None


load_dotenv()


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.5"
REQUEST_TYPES = [
    "New Booking",
    "Booking Update",
    "Appointment Update",
    "Quote Request",
    "Missing Information",
    "Cancellation",
    "Customer Request",
    "POD Request",
    "Other",
]


OPERATIONS_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "request_type": {
            "type": "string",
            "enum": REQUEST_TYPES,
        },
        "confidence_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "priority": {
            "type": "string",
            "enum": ["Low", "Normal", "High", "Urgent"],
        },
        "needs_details": {"type": "boolean"},
        "should_create_order": {"type": "boolean"},
        "should_create_quote": {"type": "boolean"},
        "reason": {"type": "string"},
        "action_required": {"type": "string"},
        "reply_body": {"type": "string"},
        "required_details": {
            "type": "array",
            "items": {"type": "string"},
        },
        "matched_reference_summary": {"type": "string"},
        "suggested_load_id": {"type": "string"},
        "load_match_confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "status_summary": {"type": "string"},
    },
    "required": [
        "request_type",
        "confidence_score",
        "priority",
        "needs_details",
        "should_create_order",
        "should_create_quote",
        "reason",
        "action_required",
        "reply_body",
        "required_details",
        "matched_reference_summary",
        "suggested_load_id",
        "load_match_confidence",
        "status_summary",
    ],
    "additionalProperties": False,
}


def _get_setting(name: str, default: str | None = None) -> str | None:
    try:
        load_dotenv()
    except Exception:
        pass

    if st is not None:
        try:
            value = st.secrets.get(name)
            if value not in [None, ""]:
                return str(value)
        except Exception:
            pass
    return os.getenv(name, default)


def _bool_setting(name: str, default: bool = False) -> bool:
    value = _get_setting(name)
    if value in [None, ""]:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_operations_ai_configured() -> bool:
    return bool(_get_setting("OPENAI_API_KEY"))


def is_operations_ai_auto_classify_enabled() -> bool:
    return is_operations_ai_configured() and _bool_setting("OPERATIONS_AI_AUTO_CLASSIFY", False)


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip() + "\n[truncated]"


def _response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"])

    parts = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()


def _parse_json_response(data: dict[str, Any]) -> dict[str, Any]:
    text = _response_text(data)
    if not text:
        raise ValueError("OpenAI returned no text output.")
    return json.loads(text)


def _normalize_suggestion(value: dict[str, Any]) -> dict[str, Any]:
    request_type = str(value.get("request_type", "Customer Request") or "Customer Request").strip()
    if request_type not in REQUEST_TYPES:
        request_type = "Customer Request"

    try:
        confidence = int(value.get("confidence_score", 0))
    except Exception:
        confidence = 0

    try:
        load_match_confidence = int(value.get("load_match_confidence", 0))
    except Exception:
        load_match_confidence = 0

    required_details = value.get("required_details", [])
    if not isinstance(required_details, list):
        required_details = []

    return {
        "request_type": request_type,
        "confidence_score": max(0, min(100, confidence)),
        "priority": str(value.get("priority", "Normal") or "Normal").strip() or "Normal",
        "needs_details": bool(value.get("needs_details", False)),
        "should_create_order": bool(value.get("should_create_order", False)),
        "should_create_quote": bool(value.get("should_create_quote", False)),
        "reason": str(value.get("reason", "") or "").strip(),
        "action_required": str(value.get("action_required", "") or "").strip(),
        "reply_body": str(value.get("reply_body", "") or "").strip(),
        "required_details": [str(item).strip() for item in required_details if str(item or "").strip()],
        "matched_reference_summary": str(value.get("matched_reference_summary", "") or "").strip(),
        "suggested_load_id": str(value.get("suggested_load_id", "") or "").strip(),
        "load_match_confidence": max(0, min(100, load_match_confidence)),
        "status_summary": str(value.get("status_summary", "") or "").strip(),
        "success": True,
        "error": "",
    }


def generate_operations_ai_suggestion(
    *,
    subject: str,
    sender: str,
    body: str,
    parsed: dict[str, Any],
    rule_classification: dict[str, Any],
    load_context: dict[str, Any] | None = None,
    load_candidates: list[dict[str, Any]] | None = None,
    feedback_examples: list[dict[str, Any]] | None = None,
    company_name: str = "CaliTrans",
) -> dict[str, Any]:
    api_key = _get_setting("OPENAI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "OPENAI_API_KEY is not configured.",
        }

    model = _get_setting("OPERATIONS_AI_MODEL", DEFAULT_MODEL)
    reasoning_effort = _get_setting("OPERATIONS_AI_REASONING_EFFORT", "low")
    timeout_seconds = int(_get_setting("OPERATIONS_AI_TIMEOUT_SECONDS", "30") or "30")

    system_prompt = f"""
You are the AI assistant for {company_name} dispatch operations.

Outcome:
Classify one customer email and draft one human-reviewed reply for the dispatcher.

Success criteria:
- Return only the requested JSON object.
- Use one of the allowed request types.
- If the customer asks for an update, POD, cancellation, appointment change, or order action but there is no booking number, container number, reference number, or matched load, classify it as Customer Request, mark needs_details true, and ask for identifying details.
- Do not classify as Quote Request unless the email includes enough pricing context such as pickup, delivery, equipment, date, or clear lane details.
- Do not classify as New Booking unless the email includes enough order details to start an order.
- For load matching, use only candidate_loads and the provided matched_load_context. Never invent a load id.
- If you select a suggested_load_id, it must exactly match one id from candidate_loads.
- Use matched_load_context for status-aware replies when available. Include only facts that are present, such as current status, current location, ETA, appointment, LFD, delivery need date, or POD/document status.
- Do not invent status, rate, appointment, POD availability, dates, charges, or promises.
- Do not mention rates, carrier pay, billing notes, or internal notes.
- Use dispatcher_feedback_examples as operating guidance. Prefer patterns the dispatch team accepted or corrected recently, but do not copy customer-specific facts from an unrelated example.
- Draft a warm, concise, professional email reply that a dispatcher can edit before sending.
- Never say the email was processed automatically. The dispatcher is reviewing it.
""".strip()

    user_context = {
        "email": {
            "from": _truncate(sender, 500),
            "subject": _truncate(subject, 500),
            "body": _truncate(body, 6000),
        },
        "parsed_fields": parsed or {},
        "rule_based_classification": rule_classification or {},
        "matched_load_context": load_context or {},
        "candidate_loads": load_candidates or [],
        "dispatcher_feedback_examples": feedback_examples or [],
        "allowed_request_types": REQUEST_TYPES,
    }

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_context, default=str)},
        ],
        "reasoning": {"effort": reasoning_effort},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "operations_inbox_ai_suggestion",
                "schema": OPERATIONS_AI_SCHEMA,
                "strict": True,
            },
        },
    }

    try:
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
        return _normalize_suggestion(_parse_json_response(json.loads(raw_body)))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            detail = ""
        return {
            "success": False,
            "error": f"{exc}: {detail}",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
