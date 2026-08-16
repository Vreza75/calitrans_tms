# ai_core/context_builder.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.text_helpers import safe_str as _safe_str

try:
    from repositories import inbox_repo, case_repo, load_repo
except Exception:  # Keeps this module import-safe during partial integration.
    inbox_repo = None
    case_repo = None
    load_repo = None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _compact_dict(data: Dict[str, Any], max_value_len: int = 1200) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key, value in (data or {}).items():
        if isinstance(value, str):
            compact[key] = value[:max_value_len]
        elif isinstance(value, (dict, list, tuple)):
            compact[key] = value
        else:
            compact[key] = value
    return compact


@dataclass
class AIContextPackage:
    intake_id: Optional[int]
    conversation_key: str
    subject: str
    sender: str
    body_preview: str
    parsed_fields: Dict[str, Any]
    email_metadata: Dict[str, Any]
    case_context: Dict[str, Any]
    load_context: Dict[str, Any]
    load_candidates: List[Dict[str, Any]]
    conversation_timeline: List[Dict[str, Any]]
    attachments: List[Dict[str, Any]]
    prior_ai_results: Dict[str, Any]
    customer_memory: Dict[str, Any]
    warnings: List[str]


class AIContextBuilder:
    """
    Builds one shared context package for CaliTrans AI agents.

    This prevents every agent from re-reading different slices of data.
    The router and agents should consume this package before calling the LLM.
    """

    AI_KEYS = [
        "_intent_agent",
        "_operations_parser_agent",
        "_load_intelligence_agent",
        "_workflow_agent",
        "_response_agent",
        "_supervisor_agent",
        "_document_parser_agent",
        "_hybrid_email_body_parser",
        "_hybrid_document_parser",
        "_ai_intake",
    ]

    ATTACHMENT_KEYS = [
        "_operations_attachments",
        "_operations_pdf_attachments",
    ]

    def build_from_record(
        self,
        *,
        record: Dict[str, Any],
        parsed: Optional[Dict[str, Any]] = None,
        body: str = "",
        load_candidates: Optional[List[Dict[str, Any]]] = None,
        customer_memory: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        parsed = _coerce_dict(parsed or record.get("parsed_data") or {})
        warnings: List[str] = []

        intake_id = self._to_int(record.get("id"))
        subject = _safe_str(record.get("source_subject"))
        sender = _safe_str(record.get("source_sender"))
        body_text = _safe_str(body or record.get("raw_text") or record.get("raw_text_preview"))
        conversation_key = self._conversation_key(record, parsed)

        case_context = self._case_context(record)
        load_context = self._load_context(record, parsed)
        if not load_context:
            warnings.append("No matched load context found.")

        timeline = self._timeline(conversation_key, intake_id)
        attachments = self._attachments(parsed, record)
        prior_ai_results = self._prior_ai_results(parsed)

        package = AIContextPackage(
            intake_id=intake_id,
            conversation_key=conversation_key,
            subject=subject,
            sender=sender,
            body_preview=body_text[:6000],
            parsed_fields=self._business_fields(parsed),
            email_metadata=self._email_metadata(record),
            case_context=case_context,
            load_context=load_context,
            load_candidates=load_candidates or [],
            conversation_timeline=timeline,
            attachments=attachments,
            prior_ai_results=prior_ai_results,
            customer_memory=customer_memory or {},
            warnings=warnings,
        )

        return asdict(package)

    def build_from_intake_id(
        self,
        intake_id: int,
        *,
        customer_memory: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if inbox_repo is None:
            return self._empty_with_warning(intake_id, "inbox_repo is not available.")

        try:
            df = inbox_repo.load_operations_inbox_record(intake_id)
        except Exception as exc:
            return self._empty_with_warning(intake_id, f"Could not load intake record: {exc}")

        if df.empty:
            return self._empty_with_warning(intake_id, "No intake record found.")

        record = df.iloc[0].to_dict()
        parsed = record.get("parsed_data") if isinstance(record.get("parsed_data"), dict) else {}
        return self.build_from_record(
            record=record,
            parsed=parsed,
            body=_safe_str(record.get("raw_text")),
            customer_memory=customer_memory or {},
        )

    def _conversation_key(self, record: Dict[str, Any], parsed: Dict[str, Any]) -> str:
        for key in ["conversation_key", "email_thread_id", "source_message_id", "email_normalized_subject", "source_subject"]:
            value = _safe_str(record.get(key))
            if value:
                return value.lower() if key == "source_subject" else value
        for key in ["Booking Number", "Container Number", "Reference Number"]:
            value = _safe_str(parsed.get(key))
            if value:
                return value
        return ""

    def _email_metadata(self, record: Dict[str, Any]) -> Dict[str, Any]:
        keys = [
            "id",
            "source_received_at",
            "source",
            "source_message_id",
            "email_direction",
            "email_mailbox",
            "email_thread_id",
            "conversation_status",
            "review_status",
            "request_type",
            "confidence_score",
            "action_required",
        ]
        return {key: record.get(key) for key in keys if key in record}

    def _case_context(self, record: Dict[str, Any]) -> Dict[str, Any]:
        case_id = self._to_int(record.get("case_id"))
        if case_id is None:
            return {}

        case_data = {}
        if case_repo is not None:
            try:
                case_data = case_repo.load_operations_case_by_id(case_id) or {}
            except Exception:
                case_data = {}

        if not case_data:
            case_data = {
                "id": case_id,
                "case_number": record.get("case_number"),
                "status": record.get("case_status"),
                "owner": record.get("case_owner"),
                "priority": record.get("case_priority"),
                "next_action": record.get("case_next_action"),
                "linked_load_id": record.get("case_linked_load_id"),
                "sla_status": record.get("case_sla_status"),
            }
        return _compact_dict(case_data)

    def _load_context(self, record: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
        load_id = self._to_int(record.get("matched_load_id") or record.get("case_linked_load_id"))
        if load_id is None:
            return {}

        if load_repo is not None and hasattr(load_repo, "load_ai_context"):
            try:
                return load_repo.load_ai_context(load_id) or {}
            except Exception:
                pass

        return {
            "Load ID": load_id,
            "Booking Number": parsed.get("Booking Number", ""),
            "Container Number": parsed.get("Container Number", ""),
            "Reference Number": parsed.get("Reference Number", ""),
            "Customer": parsed.get("Customer", ""),
            "Port": parsed.get("Port", ""),
            "Warehouse": parsed.get("Warehouse", ""),
            "Delivery Need Date": parsed.get("Delivery Need Date", ""),
            "LFD": parsed.get("LFD", ""),
        }

    def _timeline(self, conversation_key: str, intake_id: Optional[int]) -> List[Dict[str, Any]]:
        if not conversation_key or inbox_repo is None:
            return []
        try:
            df = inbox_repo.load_operations_conversation_timeline(conversation_key)
        except Exception:
            return []
        if df.empty:
            return []
        records = df.tail(12).to_dict(orient="records")
        return [_compact_dict(item, max_value_len=700) for item in records]

    def _attachments(self, parsed: Dict[str, Any], record: Dict[str, Any]) -> List[Dict[str, Any]]:
        attachments: List[Dict[str, Any]] = []
        for key in self.ATTACHMENT_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                attachments.extend([item for item in value if isinstance(item, dict)])

        filename = _safe_str(record.get("filename"))
        file_path = _safe_str(record.get("file_path"))
        if filename and file_path:
            attachments.append({"filename": filename, "file_path": file_path})

        cleaned = []
        seen = set()
        for item in attachments:
            marker = (_safe_str(item.get("filename")), _safe_str(item.get("file_path")))
            if marker in seen:
                continue
            seen.add(marker)
            cleaned.append(_compact_dict(item, max_value_len=800))
        return cleaned

    def _prior_ai_results(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        return {key: parsed.get(key) for key in self.AI_KEYS if key in parsed}

    def _business_fields(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in parsed.items() if not str(key).startswith("_")}

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        value_text = _safe_str(value)
        if not value_text:
            return None
        try:
            return int(float(value_text))
        except Exception:
            return None

    def _empty_with_warning(self, intake_id: Optional[int], warning: str) -> Dict[str, Any]:
        return asdict(AIContextPackage(
            intake_id=intake_id,
            conversation_key="",
            subject="",
            sender="",
            body_preview="",
            parsed_fields={},
            email_metadata={},
            case_context={},
            load_context={},
            load_candidates=[],
            conversation_timeline=[],
            attachments=[],
            prior_ai_results={},
            customer_memory={},
            warnings=[warning],
        ))


def build_ai_context_from_record(**kwargs) -> Dict[str, Any]:
    return AIContextBuilder().build_from_record(**kwargs)


def build_ai_context_from_intake_id(intake_id: int, **kwargs) -> Dict[str, Any]:
    return AIContextBuilder().build_from_intake_id(intake_id, **kwargs)
