# ui_components/dispatcher_action_panel.py

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from utils.text_helpers import safe_str as _safe_str


ACTION_OPTIONS = [
    "Approve AI Recommendation",
    "Edit Before Applying",
    "Reject Recommendation",
    "Create Follow-up Task",
    "Attach to Load",
    "Update Load Fields",
    "Draft Reply",
    "Mark Waiting Customer",
    "Close / No Action",
]


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _format_confidence(value: Any) -> str:
    try:
        score = int(float(value or 0))
    except Exception:
        score = 0
    if score >= 85:
        return f"High ({score}%)"
    if score >= 65:
        return f"Medium ({score}%)"
    return f"Review ({score}%)"


def _get_agent_section(ai_result: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for name in names:
        value = ai_result.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _extract_recommendation(parsed: Dict[str, Any]) -> Dict[str, Any]:
    workflow = _get_agent_section(parsed, "_workflow_agent", "workflow")
    load_ai = _get_agent_section(parsed, "_load_intelligence_agent", "load_intelligence")
    response = _get_agent_section(parsed, "_response_agent", "response")
    intent = _get_agent_section(parsed, "_intent_agent", "intent")

    return {
        "intent": intent.get("primary_intent") or intent.get("intent") or parsed.get("request_type", ""),
        "language": intent.get("language") or response.get("language") or "",
        "confidence": workflow.get("confidence") or load_ai.get("confidence") or intent.get("confidence") or parsed.get("confidence_score", 0),
        "next_action": workflow.get("next_action") or workflow.get("recommended_action") or parsed.get("action_required", ""),
        "owner": workflow.get("owner") or workflow.get("department_owner") or intent.get("department_owner", ""),
        "draft_reply": response.get("draft_reply") or response.get("reply_body") or response.get("draft_body") or "",
        "matched_load_id": load_ai.get("matched_load_id") or parsed.get("matched_load_id", ""),
        "match_reason": load_ai.get("match_reason") or load_ai.get("reason", ""),
        "warnings": workflow.get("warnings") or load_ai.get("warnings") or [],
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if _safe_str(value):
            return value
    return ""


def _guess_selected_id(record: Dict[str, Any], operations_case: Dict[str, Any]) -> int:
    for key in ("id", "intake_id", "operations_inbox_id", "source_id"):
        value = record.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                pass
    for key in ("id", "intake_id", "operations_inbox_id"):
        value = operations_case.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                pass
    return 0


def render_dispatcher_action_panel(
    *,
    selected_id: Optional[int] = None,
    record: Optional[Dict[str, Any]] = None,
    parsed: Optional[Dict[str, Any]] = None,
    operations_case: Optional[Dict[str, Any]] = None,
    matched_load_id: Any = None,
    on_action=None,
    # Compatibility aliases used by pages_app/dispatcher_workspace.py.
    case: Optional[Dict[str, Any]] = None,
    load: Optional[Dict[str, Any]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Dispatcher-facing action panel for one selected Operations Inbox item/case.

    Supports both call styles currently used in the project:
    1. Operations Inbox style: selected_id, record, parsed, operations_case.
    2. Dispatcher Workspace style: record, case, load, tasks.

    This component does not update the database by itself. It returns an action
    payload that the page/service can apply through repositories.
    """

    record = record or {}
    operations_case = operations_case or case or {}
    load = load or {}
    tasks = tasks or []

    if parsed is None:
        parsed = _coerce_dict(record.get("parsed_data"))
        if not parsed:
            parsed = _coerce_dict(record.get("ai_result"))
        if not parsed:
            parsed = _coerce_dict(operations_case.get("parsed_data"))
    parsed = parsed or {}

    if selected_id is None:
        selected_id = _guess_selected_id(record, operations_case)

    if not matched_load_id:
        matched_load_id = _first_present(
            load.get("id"),
            load.get("load_id"),
            load.get("booking_number"),
            record.get("matched_load_id"),
            operations_case.get("matched_load_id"),
        )

    recommendation = _extract_recommendation(parsed)

    st.markdown("### Dispatcher Action Panel")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Intent", _safe_str(recommendation.get("intent")) or _safe_str(record.get("request_type")) or "Review")
    c2.metric("Owner", _safe_str(recommendation.get("owner")) or _safe_str(operations_case.get("owner")) or "Dispatch")
    c3.metric("Confidence", _format_confidence(recommendation.get("confidence")))
    c4.metric("Load", matched_load_id or recommendation.get("matched_load_id") or "-")

    next_action = _safe_str(recommendation.get("next_action"))
    if next_action:
        st.info(next_action)

    warnings = recommendation.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    for warning in warnings[:4]:
        if _safe_str(warning):
            st.warning(_safe_str(warning))

    with st.expander("Proposed Operational Details", expanded=True):
        rows: List[Dict[str, str]] = []
        field_map = {
            "Customer": "Customer",
            "customer": "Customer",
            "Booking Number": "Booking",
            "booking_number": "Booking",
            "Reference Number": "Reference",
            "reference_number": "Reference",
            "Container Number": "Container",
            "container_number": "Container",
            "TYPE": "Move Type",
            "type": "Move Type",
            "Port": "Port",
            "port": "Port",
            "Warehouse": "Warehouse",
            "warehouse": "Warehouse",
            "Address": "Address",
            "address": "Address",
            "Delivery Need Date": "Delivery Need Date",
            "delivery_need_date": "Delivery Need Date",
            "LFD": "LFD",
            "lfd": "LFD",
            "Document Cutoff": "Cutoff",
            "document_cutoff": "Cutoff",
        }
        combined_sources = [parsed, load, record]
        seen_labels = set()
        for source_field, label in field_map.items():
            if label in seen_labels:
                continue
            value = ""
            for source in combined_sources:
                value = _safe_str(source.get(source_field, ""))
                if value:
                    break
            if value:
                rows.append({"Field": label, "Value": value})
                seen_labels.add(label)

        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No structured operational fields found yet.")

    if tasks:
        with st.expander("Related Tasks", expanded=False):
            task_rows = []
            for task in tasks[:25]:
                if isinstance(task, dict):
                    task_rows.append(
                        {
                            "Task": _safe_str(task.get("title") or task.get("task") or task.get("description")),
                            "Status": _safe_str(task.get("status")),
                            "Owner": _safe_str(task.get("owner") or task.get("assigned_to")),
                        }
                    )
            if task_rows:
                st.dataframe(pd.DataFrame(task_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No related task details available.")

    draft_reply = _safe_str(recommendation.get("draft_reply"))
    if draft_reply:
        with st.expander("Draft Reply", expanded=False):
            st.text_area(
                "AI Draft",
                value=draft_reply,
                height=180,
                key=f"dispatcher_action_draft_reply_{selected_id}",
            )

    with st.form(f"dispatcher_action_form_{selected_id}"):
        action = st.selectbox("Dispatcher Action", ACTION_OPTIONS)
        decision_note = st.text_area(
            "Decision / Correction Notes",
            value="",
            height=90,
            placeholder="Example: AI matched the right load, but warehouse should be updated before replying.",
        )
        assign_owner = st.selectbox(
            "Owner",
            ["Dispatch", "Accounting", "Manager", "Customer Service", "Operations", "Safety"],
            index=0,
        )
        priority = st.selectbox("Priority", ["Medium", "High", "Critical", "Low"], index=0)
        submitted = st.form_submit_button("Save Dispatcher Decision")

    if not submitted:
        return None

    payload = {
        "selected_id": int(selected_id or 0),
        "case_id": operations_case.get("id"),
        "matched_load_id": matched_load_id or recommendation.get("matched_load_id"),
        "action": action,
        "owner": assign_owner,
        "priority": priority,
        "decision_note": decision_note,
        "recommendation": recommendation,
    }

    if callable(on_action):
        on_action(payload)

    return payload
