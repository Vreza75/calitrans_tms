# ui_components/case_summary_panel.py

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd
import streamlit as st


def _safe_str(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return int(float(value))
    except Exception:
        return default


def _format_dt(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return "-"
    return parsed.strftime("%Y-%m-%d %I:%M %p")


def _value_from(record: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = _safe_str(record.get(key, ""))
        if value:
            return value
    return default


def _status_badge(label: str) -> str:
    label = _safe_str(label) or "Open"
    normalized = label.lower()

    if "closed" in normalized or "resolved" in normalized:
        bg = "#dcfce7"
        fg = "#166534"
    elif "waiting" in normalized:
        bg = "#fef3c7"
        fg = "#92400e"
    elif "overdue" in normalized or "critical" in normalized:
        bg = "#fee2e2"
        fg = "#991b1b"
    elif "attached" in normalized:
        bg = "#dbeafe"
        fg = "#1d4ed8"
    else:
        bg = "#f1f5f9"
        fg = "#334155"

    return (
        f"<span style='display:inline-block;padding:4px 9px;border-radius:999px;"
        f"background:{bg};color:{fg};font-size:0.76rem;font-weight:700;'>"
        f"{label}</span>"
    )


def render_case_summary_panel(
    *,
    operations_case: Optional[Dict[str, Any]] = None,
    record: Optional[Dict[str, Any]] = None,
    parsed: Optional[Dict[str, Any]] = None,
    tokens: Optional[Dict[str, Any]] = None,
    matched_load_id: Any = None,
    timeline_df: Optional[pd.DataFrame] = None,
    expanded: bool = True,
) -> None:
    """
    Dispatcher-friendly Operations Case summary panel.

    This component is UI-only. It does not query the database and does not update data.

    Expected usage inside Operations Inbox:

        render_case_summary_panel(
            operations_case=operations_case,
            record=record.to_dict() if hasattr(record, "to_dict") else record,
            parsed=parsed,
            tokens=tokens,
            matched_load_id=matched_load_id,
            timeline_df=timeline_df,
        )
    """

    operations_case = operations_case or {}
    record = record or {}
    parsed = parsed or {}
    tokens = tokens or {}

    case_number = _value_from(operations_case, "case_number", default="No Case")
    case_status = _value_from(operations_case, "status", "case_status", default=_value_from(record, "case_status", default="Open"))
    owner = _value_from(operations_case, "owner", "case_owner", default=_value_from(record, "case_owner", default="Unassigned"))
    priority = _value_from(operations_case, "priority", "case_priority", default=_value_from(record, "case_priority", default="Medium"))
    request_type = _value_from(operations_case, "request_type", default=_value_from(record, "request_type", "request_type_clean", default="Customer Request"))
    next_action = _value_from(operations_case, "next_action", default=_value_from(record, "action_required", default="Review request."))
    customer = _value_from(
        operations_case,
        "customer",
        "case_customer",
        default=(
            _safe_str(parsed.get("Customer", ""))
            or _value_from(record, "case_customer", "source_sender", default="-")
        ),
    )

    linked_load_id = (
        _safe_str(matched_load_id)
        or _value_from(operations_case, "linked_load_id", "case_linked_load_id")
        or _value_from(record, "matched_load_id")
        or "-"
    )

    booking = (
        _safe_str(parsed.get("Booking Number", ""))
        or _safe_str(tokens.get("booking_number", ""))
        or "-"
    )
    container = (
        _safe_str(parsed.get("Container Number", ""))
        or _safe_str(tokens.get("container_number", ""))
        or "-"
    )
    reference = (
        _safe_str(parsed.get("Reference Number", ""))
        or _safe_str(tokens.get("reference_number", ""))
        or "-"
    )

    sla_status = _value_from(operations_case, "sla_status", default=_value_from(record, "case_sla_status", default="On Track"))
    message_count = _safe_int(
        operations_case.get("message_count", record.get("case_message_count", record.get("conversation_message_count", 0)))
    )
    last_message_at = _format_dt(
        operations_case.get("last_message_at")
        or record.get("case_last_message_at")
        or record.get("source_received_at")
        or record.get("created_at")
    )

    with st.expander(f"Case Summary - {case_number}", expanded=expanded):
        st.markdown(
            f"""
            <div style="border:1px solid #d8e0ec;border-radius:10px;background:#ffffff;padding:14px 14px 10px 14px;margin-bottom:10px;">
                <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
                    <span style="font-size:1.05rem;font-weight:800;color:#0f172a;">{case_number}</span>
                    {_status_badge(case_status)}
                    {_status_badge(priority)}
                    {_status_badge(sla_status)}
                </div>
                <div style="font-size:0.86rem;color:#475569;line-height:1.45;">
                    <b>Next action:</b> {next_action or 'Review request.'}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Customer", customer or "-")
        c2.metric("Owner", owner or "Unassigned")
        c3.metric("Request Type", request_type or "-")
        c4.metric("Linked Load", linked_load_id)

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Booking", booking)
        c6.metric("Container", container)
        c7.metric("Reference", reference)
        c8.metric("Messages", message_count)

        st.caption(f"Last message: {last_message_at}")

        detail_rows = [
            {"Field": "Case Number", "Value": case_number},
            {"Field": "Status", "Value": case_status},
            {"Field": "Owner", "Value": owner},
            {"Field": "Priority", "Value": priority},
            {"Field": "SLA", "Value": sla_status},
            {"Field": "Request Type", "Value": request_type},
            {"Field": "Customer", "Value": customer},
            {"Field": "Linked Load", "Value": linked_load_id},
            {"Field": "Booking", "Value": booking},
            {"Field": "Container", "Value": container},
            {"Field": "Reference", "Value": reference},
            {"Field": "Next Action", "Value": next_action},
        ]

        with st.expander("Case Details", expanded=False):
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

        if timeline_df is not None and not timeline_df.empty:
            with st.expander("Recent Timeline", expanded=False):
                display_df = timeline_df.copy().tail(8)
                if "event_at" in display_df.columns:
                    display_df["Time"] = pd.to_datetime(display_df["event_at"], errors="coerce").dt.strftime("%Y-%m-%d %I:%M %p")
                elif "source_received_at" in display_df.columns:
                    display_df["Time"] = pd.to_datetime(display_df["source_received_at"], errors="coerce").dt.strftime("%Y-%m-%d %I:%M %p")
                else:
                    display_df["Time"] = ""

                preferred_cols = [
                    col for col in ["Time", "event_type", "actor", "title", "details", "source_subject", "message_preview"]
                    if col in display_df.columns
                ]
                if preferred_cols:
                    st.dataframe(display_df[preferred_cols], use_container_width=True, hide_index=True)
                else:
                    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_case_empty_state() -> None:
    st.info(
        "No Operations Case is open for this work item yet. Open a case when the email needs managed follow-up, tracking, or load-related action."
    )
