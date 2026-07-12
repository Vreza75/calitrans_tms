# ui_components/ai_review_panel.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


AGENT_KEYS = [
    "_intent_agent",
    "_operations_parser_agent",
    "_load_intelligence_agent",
    "_workflow_agent",
    "_response_agent",
    "_supervisor_agent",
    "_hybrid_email_body_parser",
    "_hybrid_document_parser",
    "_document_parser_agent",
]


def _safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _confidence_label(score: Any) -> str:
    score_float = _safe_float(score, 0)
    if score_float <= 1:
        score_float *= 100
    score_int = int(round(score_float))

    if score_int >= 85:
        return f"High ({score_int}%)"
    if score_int >= 65:
        return f"Medium ({score_int}%)"
    if score_int > 0:
        return f"Low ({score_int}%)"
    return "Unknown"


def _first_value(*values: Any) -> str:
    for value in values:
        text = _safe_str(value)
        if text:
            return text
    return ""


def _get_agent(parsed: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = parsed.get(key, {}) if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _extract_proposed_updates(parsed: Dict[str, Any]) -> Dict[str, Any]:
    parser_agent = _get_agent(parsed, "_operations_parser_agent")
    doc_agent = _get_agent(parsed, "_document_parser_agent")
    hybrid_doc = _get_agent(parsed, "_hybrid_document_parser")
    hybrid_email = _get_agent(parsed, "_hybrid_email_body_parser")

    updates = {}

    for candidate in [
        parser_agent.get("proposed_updates"),
        doc_agent.get("parsed_fields"),
        hybrid_doc.get("parsed_fields"),
        hybrid_email.get("parsed_fields"),
    ]:
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if key.startswith("_"):
                    continue
                if _safe_str(value) and not _safe_str(updates.get(key)):
                    updates[key] = value

    return updates


def _extract_warnings(parsed: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    for key in AGENT_KEYS:
        agent = _get_agent(parsed, key)
        raw = agent.get("warnings") or agent.get("warning") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            warnings.extend([_safe_str(item) for item in raw if _safe_str(item)])

    supervisor = _get_agent(parsed, "_supervisor_agent")
    for key in ["concerns", "issues", "risk_flags"]:
        raw = supervisor.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            warnings.extend([_safe_str(item) for item in raw if _safe_str(item)])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for item in warnings:
        lowered = item.lower()
        if lowered not in seen:
            unique.append(item)
            seen.add(lowered)
    return unique


def build_ai_review_summary(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts raw agent JSON stored in parsed_data into a dispatcher-friendly summary.
    Safe to call even when some agents have not run yet.
    """

    intent = _get_agent(parsed, "_intent_agent")
    parser = _get_agent(parsed, "_operations_parser_agent")
    load = _get_agent(parsed, "_load_intelligence_agent")
    workflow = _get_agent(parsed, "_workflow_agent")
    response = _get_agent(parsed, "_response_agent")
    supervisor = _get_agent(parsed, "_supervisor_agent")

    confidence = _first_value(
        supervisor.get("confidence"),
        workflow.get("confidence"),
        load.get("confidence"),
        parser.get("confidence"),
        intent.get("confidence"),
        parsed.get("ai_confidence"),
    )

    return {
        "summary": _first_value(
            response.get("summary"),
            workflow.get("summary"),
            parser.get("summary"),
            intent.get("summary"),
            parsed.get("ai_summary"),
            "AI review is available. Review proposed updates before applying changes.",
        ),
        "intent": _first_value(
            intent.get("primary_intent"),
            intent.get("request_type"),
            parsed.get("ai_intent"),
            parsed.get("request_type"),
        ),
        "language": _first_value(
            intent.get("language"),
            response.get("language"),
            parsed.get("ai_language"),
        ),
        "department_owner": _first_value(
            workflow.get("department_owner"),
            intent.get("department_owner"),
            parsed.get("ai_department_owner"),
        ),
        "next_action": _first_value(
            workflow.get("next_action"),
            workflow.get("recommended_action"),
            intent.get("recommended_action"),
            parsed.get("ai_recommended_action"),
        ),
        "matched_load_id": _first_value(
            load.get("matched_load_id"),
            load.get("recommended_load_id"),
            parsed.get("matched_load_id"),
        ),
        "match_reason": _first_value(
            load.get("match_reason"),
            load.get("reason"),
        ),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "draft_reply": _first_value(
            response.get("draft_reply"),
            response.get("reply_body"),
            response.get("response"),
            parsed.get("draft_reply"),
        ),
        "proposed_updates": _extract_proposed_updates(parsed),
        "warnings": _extract_warnings(parsed),
        "supervisor_decision": _first_value(
            supervisor.get("decision"),
            supervisor.get("approval_status"),
        ),
    }


def render_proposed_updates_table(updates: Dict[str, Any]) -> None:
    if not updates:
        st.info("No proposed field updates were found.")
        return

    rows = [
        {"Field": key, "Proposed Value": value}
        for key, value in updates.items()
        if _safe_str(value)
    ]

    if not rows:
        st.info("No proposed field updates were found.")
        return

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_ai_review_panel(
    *,
    parsed: Dict[str, Any],
    intake_id: Optional[int] = None,
    on_run_agents=None,
    show_raw_json: bool = False,
) -> Dict[str, Any]:
    """
    Dispatcher-friendly AI review panel.

    Parameters:
        parsed: parsed_data dictionary from order_intake.
        intake_id: optional inbox/intake id for button keys.
        on_run_agents: optional callable. If supplied, clicking Run AI Agents calls it.
        show_raw_json: if True, raw agent JSON expanders are shown by default.

    Returns:
        A small dict describing dispatcher button actions.
    """

    parsed = parsed or {}
    summary = build_ai_review_summary(parsed)
    action_state = {
        "run_agents_clicked": False,
        "approve_clicked": False,
        "edit_clicked": False,
        "reject_clicked": False,
    }

    st.markdown("### AI Review Panel")

    top_cols = st.columns([1.2, 1, 1, 1])
    top_cols[0].metric("Intent", summary.get("intent") or "Unknown")
    top_cols[1].metric("Language", summary.get("language") or "Unknown")
    top_cols[2].metric("Confidence", summary.get("confidence_label") or "Unknown")
    top_cols[3].metric("Owner", summary.get("department_owner") or "-")

    if summary.get("summary"):
        st.markdown("#### AI Summary")
        st.info(summary["summary"])

    if summary.get("next_action"):
        st.markdown("#### Recommended Next Action")
        st.success(summary["next_action"])

    if summary.get("matched_load_id"):
        st.markdown("#### Load Match")
        st.write(f"**Matched Load:** {summary['matched_load_id']}")
        if summary.get("match_reason"):
            st.caption(summary["match_reason"])

    warnings = summary.get("warnings") or []
    if warnings:
        st.markdown("#### Warnings / Review Flags")
        for warning in warnings:
            st.warning(warning)

    with st.expander("Proposed Load / Case Updates", expanded=True):
        render_proposed_updates_table(summary.get("proposed_updates") or {})

    with st.expander("Draft Customer Reply", expanded=bool(summary.get("draft_reply"))):
        draft_reply = summary.get("draft_reply") or ""
        if draft_reply:
            st.text_area(
                "Draft Reply",
                value=draft_reply,
                height=180,
                key=f"ai_review_draft_reply_{intake_id or 'new'}",
            )
        else:
            st.info("No draft reply was generated yet.")

    button_cols = st.columns(4)

    if button_cols[0].button(
        "Run AI Agents",
        key=f"ai_review_run_agents_{intake_id or 'new'}",
        use_container_width=True,
    ):
        action_state["run_agents_clicked"] = True
        if callable(on_run_agents):
            on_run_agents()

    if button_cols[1].button(
        "Approve",
        key=f"ai_review_approve_{intake_id or 'new'}",
        use_container_width=True,
    ):
        action_state["approve_clicked"] = True

    if button_cols[2].button(
        "Edit",
        key=f"ai_review_edit_{intake_id or 'new'}",
        use_container_width=True,
    ):
        action_state["edit_clicked"] = True

    if button_cols[3].button(
        "Reject",
        key=f"ai_review_reject_{intake_id or 'new'}",
        use_container_width=True,
    ):
        action_state["reject_clicked"] = True

    with st.expander("Raw Agent JSON", expanded=show_raw_json):
        found_any = False
        for key in AGENT_KEYS:
            if key in parsed:
                found_any = True
                with st.expander(key.replace("_", " ").title(), expanded=False):
                    st.json(parsed[key])
        if not found_any:
            st.info("No saved AI agent results found for this email yet.")

    return action_state
