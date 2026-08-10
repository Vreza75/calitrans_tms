from __future__ import annotations

import services.operations_inbox_service as ops


def default_operations_reply_subject(subject: str, request_type: str) -> str:
    return ops.default_operations_reply_subject(subject, request_type)


def default_operations_action_subject(subject: str, request_type: str, action_mode: str) -> str:
    return ops.default_operations_action_subject(subject, request_type, action_mode)


def apply_operations_reply_tone(body: str, tone: str, language: str) -> str:
    # The current safe fallback keeps dispatcher-edited body unchanged.
    return body


def default_operations_reply_body(*args, **kwargs) -> str:
    return ops.default_operations_reply_body(*args, **kwargs)


def default_operations_reply_body_english(*args, **kwargs) -> str:
    return ops.default_operations_reply_body(*args, **kwargs)


def default_operations_reply_body_spanish(*args, **kwargs) -> str:
    kwargs["reply_language"] = "Spanish"
    return ops.default_operations_reply_body(*args, **kwargs)


def save_operations_email_reply(**kwargs) -> None:
    return ops.save_operations_email_reply(**kwargs)


def insert_operations_thread_reply_record(**kwargs) -> None:
    return ops.insert_operations_thread_reply_record(**kwargs)


_default_operations_reply_subject = default_operations_reply_subject
_default_operations_action_subject = default_operations_action_subject
_apply_operations_reply_tone = apply_operations_reply_tone
_default_operations_reply_body = default_operations_reply_body
_default_operations_reply_body_english = default_operations_reply_body_english
_default_operations_reply_body_spanish = default_operations_reply_body_spanish
_save_operations_email_reply = save_operations_email_reply
_insert_operations_thread_reply_record = insert_operations_thread_reply_record
