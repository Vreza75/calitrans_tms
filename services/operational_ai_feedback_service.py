from __future__ import annotations

import services.operations_inbox_service as ops


def ensure_operations_ai_feedback_table() -> None:
    return ops.ensure_operations_ai_feedback_table()


def truncate_feedback_text(value, limit: int = 700) -> str:
    return ops.truncate_feedback_text(value, limit)


def recent_operations_ai_feedback_examples(limit: int = 6) -> list[dict]:
    return ops.recent_operations_ai_feedback_examples(limit)


def feedback_sender_domain(sender: str) -> str:
    email = ops.extract_email_address(sender).lower()
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def save_operations_ai_feedback(**kwargs) -> None:
    return ops.save_operations_ai_feedback(**kwargs)


def operations_ai_rule_context(classification: dict, parsed: dict, subject: str, body: str) -> dict:
    return ops.operations_ai_rule_context(classification, parsed, subject, body)


def conversation_key_from_candidate(candidate: dict, fallback: str) -> str:
    return ops.conversation_key_from_candidate(candidate, fallback)


_ensure_operations_ai_feedback_table = ensure_operations_ai_feedback_table
_truncate_feedback_text = truncate_feedback_text
_recent_operations_ai_feedback_examples = recent_operations_ai_feedback_examples
_feedback_sender_domain = feedback_sender_domain
_save_operations_ai_feedback = save_operations_ai_feedback
_operations_ai_rule_context = operations_ai_rule_context
_conversation_key_from_candidate = conversation_key_from_candidate
