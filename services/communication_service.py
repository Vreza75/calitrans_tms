from __future__ import annotations

from typing import Any

import pandas as pd

from db_client import execute


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "nat", "null"}:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def save_load_communication(
    load_id,
    intake_id=None,
    conversation_key: str = "",
    request_type: str = "Customer Request",
    subject: str = "",
    sender: str = "",
    body: str = "",
    direction: str = "inbound",
    case_id=None,
) -> None:
    execute(
        """
        insert into load_communications (
            load_id,
            intake_id,
            case_id,
            conversation_key,
            communication_type,
            direction,
            subject,
            sender,
            message_body
        )
        values (
            :load_id,
            :intake_id,
            :case_id,
            :conversation_key,
            :communication_type,
            :direction,
            :subject,
            :sender,
            :message_body
        )
        """,
        {
            "load_id": int_or_none(load_id),
            "intake_id": int_or_none(intake_id),
            "case_id": int_or_none(case_id),
            "conversation_key": conversation_key or None,
            "communication_type": request_type,
            "direction": direction,
            "subject": subject,
            "sender": sender,
            "message_body": body,
        },
    )


_int_or_none = int_or_none
_save_load_communication = save_load_communication
