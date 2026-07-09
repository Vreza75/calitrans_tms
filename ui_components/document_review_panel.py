# ui_components/document_review_panel.py

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st


DOCUMENT_ORDER_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Size",
    "Port",
    "Terminal",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "Pickup Appointment",
    "Delivery Appointment",
    "LFD",
    "Document Cutoff",
    "Reefer Temperature",
    "Commodity",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Dispatcher Notes",
]


def _safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_doc_agent_meta(document_parsed: Dict[str, Any]) -> Dict[str, Any]:
    hybrid = _as_dict(document_parsed.get("_hybrid_document_parser"))
    if not hybrid:
        return {}

    agent = _as_dict(hybrid.get("document_parser_agent"))
    return {
        "parser_used": hybrid.get("parser_used", ""),
        "confidence": hybrid.get("confidence", ""),
        "needs_review": hybrid.get("needs_review", True),
        "warnings": hybrid.get("warnings", []) or agent.get("warnings", []),
        "summary": agent.get("summary", "") or hybrid.get("summary", ""),
        "vendor_detected": agent.get("vendor_detected", ""),
        "document_type": agent.get("document_type", ""),
    }


def build_document_comparison_rows(
    *,
    email_parsed: Dict[str, Any],
    document_parsed: Dict[str, Any],
    fields: List[str] | None = None,
) -> Tuple[List[Dict[str, str]], List[str], Dict[str, str]]:
    """
    Builds rows comparing email body fields vs document/PDF fields.

    Returns:
    - rows for dataframe display
    - conflict field names
    - final merged values
    """
    fields = fields or DOCUMENT_ORDER_FIELDS
    rows: List[Dict[str, str]] = []
    conflicts: List[str] = []
    final_values: Dict[str, str] = {}

    for field in fields:
        email_value = _safe_str(email_parsed.get(field, ""))
        document_value = _safe_str(document_parsed.get(field, ""))

        if field == "Dispatcher Notes" and email_value and document_value:
            final_value = email_value if document_value in email_value else f"{email_value}\n{document_value}"
            status = "Combined"
        elif email_value and document_value and email_value.lower() != document_value.lower():
            final_value = document_value
            status = "Review mismatch"
            conflicts.append(field)
        elif document_value:
            final_value = document_value
            status = "Document"
        elif email_value:
            final_value = email_value
            status = "Email"
        else:
            final_value = ""
            status = "Blank"

        final_values[field] = final_value
        rows.append(
            {
                "Field": field,
                "Email Body": email_value,
                "Document": document_value,
                "Final Value": final_value,
                "Status": status,
            }
        )

    return rows, conflicts, final_values


def render_document_review_panel(
    *,
    intake_id: int,
    parsed: Dict[str, Any],
    attachment: Dict[str, Any] | None = None,
    email_parsed: Dict[str, Any] | None = None,
    document_parsed: Dict[str, Any] | None = None,
    expanded: bool = True,
    allow_edit: bool = True,
) -> Dict[str, Any]:
    """
    Dispatcher-facing document comparison panel.

    This component does not write to the database by itself.
    It returns dispatcher decisions so the page/service can save them.

    Expected integration:
        result = render_document_review_panel(...)
        if result["action"] == "save":
            ops.store_operations_parsed_data(intake_id, result["final_values"])
    """
    parsed = _as_dict(parsed)
    attachment = _as_dict(attachment)
    email_parsed = _as_dict(email_parsed) or parsed
    document_parsed = _as_dict(document_parsed) or _as_dict(attachment.get("parsed_data"))

    result: Dict[str, Any] = {
        "action": "none",
        "final_values": {},
        "conflicts": [],
        "selected_attachment": attachment,
    }

    title_suffix = _safe_str(attachment.get("filename", ""))
    title = "Document Review" if not title_suffix else f"Document Review - {title_suffix}"

    with st.expander(title, expanded=expanded):
        if not attachment and not document_parsed:
            st.info("No parsed document is available for this request yet.")
            return result

        meta = _extract_doc_agent_meta(document_parsed)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Parser", _safe_str(meta.get("parser_used", "rule/parser")) or "rule/parser")
        confidence = meta.get("confidence", "")
        m2.metric("Confidence", f"{int(float(confidence) * 100)}%" if isinstance(confidence, float) and confidence <= 1 else (_safe_str(confidence) or "-"))
        m3.metric("Needs Review", "Yes" if meta.get("needs_review", False) else "No")
        m4.metric("Fields Found", sum(1 for field in DOCUMENT_ORDER_FIELDS if _safe_str(document_parsed.get(field, ""))))

        if meta.get("summary"):
            st.write(f"**AI Summary:** {meta.get('summary')}")
        if meta.get("vendor_detected") or meta.get("document_type"):
            st.caption(
                f"Vendor: {_safe_str(meta.get('vendor_detected', '')) or '-'} | "
                f"Document Type: {_safe_str(meta.get('document_type', '')) or '-'}"
            )

        warnings = meta.get("warnings") or []
        if warnings:
            st.warning("Warnings: " + "; ".join([_safe_str(w) for w in warnings if _safe_str(w)]))

        rows, conflicts, final_values = build_document_comparison_rows(
            email_parsed=email_parsed,
            document_parsed=document_parsed,
        )
        result["conflicts"] = conflicts
        result["final_values"] = final_values

        if conflicts:
            st.error("Review mismatches: " + ", ".join(conflicts))
        else:
            st.success("No document/email field conflicts detected.")

        comparison_df = pd.DataFrame(rows)
        st.dataframe(comparison_df, use_container_width=True, hide_index=True)

        if allow_edit:
            st.markdown("#### Final Values")
            edited_values: Dict[str, str] = {}
            left, right = st.columns(2)
            for index, field in enumerate(DOCUMENT_ORDER_FIELDS):
                target = left if index % 2 == 0 else right
                with target:
                    if field == "Dispatcher Notes":
                        edited_values[field] = st.text_area(
                            field,
                            value=final_values.get(field, ""),
                            height=90,
                            key=f"doc_review_{intake_id}_{field}",
                        )
                    else:
                        edited_values[field] = st.text_input(
                            field,
                            value=final_values.get(field, ""),
                            key=f"doc_review_{intake_id}_{field}",
                        )

            result["final_values"] = edited_values

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Approve Document Fields", key=f"approve_document_fields_{intake_id}", use_container_width=True):
                result["action"] = "save"
        with c2:
            if st.button("Mark Needs Review", key=f"document_needs_review_{intake_id}", use_container_width=True):
                result["action"] = "needs_review"
        with c3:
            if st.button("Reject Document Parse", key=f"reject_document_parse_{intake_id}", use_container_width=True):
                result["action"] = "reject"

        with st.expander("Raw Document Parser Data", expanded=False):
            st.json(document_parsed)

    return result
