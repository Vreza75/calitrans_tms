# ui_components/document_review_panel.py

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from services.operations_field_service import reconcile_parsed_sources
from utils.text_helpers import safe_str as _safe_str

DOCUMENT_ORDER_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Container Numbers",
    "Container Qty",
    "Size",
    "Port",
    "Terminal",
    "Port PIN",
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
    "Contact Title",
    "Contact Company",
    "Contact Email",
    "Contact Phone",
    "Dispatcher Notes",
]


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
    final_values, rows, conflicts = reconcile_parsed_sources(
        email_parsed,
        document_parsed,
        fields=fields or DOCUMENT_ORDER_FIELDS,
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

        rows, conflicts, final_values = build_document_comparison_rows(
            email_parsed=email_parsed,
            document_parsed=document_parsed,
        )
        effective_needs_review = bool(
            meta.get("needs_review", False)
            or conflicts
            or final_values.get("_needs_review", False)
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Parser", _safe_str(meta.get("parser_used", "rule/parser")) or "rule/parser")
        confidence = meta.get("confidence", "")
        m2.metric("Confidence", f"{int(float(confidence) * 100)}%" if isinstance(confidence, float) and confidence <= 1 else (_safe_str(confidence) or "-"))
        m3.metric("Needs Review", "Yes" if effective_needs_review else "No")
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

        result["conflicts"] = conflicts
        result["final_values"] = final_values
        result["needs_review"] = effective_needs_review

        if conflicts:
            st.error("Review mismatches: " + ", ".join(conflicts))
        else:
            st.success("No document/email field conflicts detected.")

        comparison_df = pd.DataFrame(rows)
        st.dataframe(comparison_df, width="stretch", hide_index=True)

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
                    elif field == "Container Numbers":
                        container_values = final_values.get(field, [])
                        if isinstance(container_values, list):
                            container_text = ", ".join(str(value) for value in container_values)
                        else:
                            container_text = _safe_str(container_values)
                        edited_container_text = st.text_input(
                            field,
                            value=container_text,
                            key=f"doc_review_{intake_id}_{field}",
                        )
                        edited_values[field] = [
                            value.strip().upper()
                            for value in edited_container_text.split(",")
                            if value.strip()
                        ]
                    else:
                        edited_values[field] = st.text_input(
                            field,
                            value=final_values.get(field, ""),
                            key=f"doc_review_{intake_id}_{field}",
                        )

            result["final_values"] = edited_values

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Approve Document Fields", key=f"approve_document_fields_{intake_id}", width="stretch"):
                result["action"] = "save"
        with c2:
            if st.button("Mark Needs Review", key=f"document_needs_review_{intake_id}", width="stretch"):
                result["action"] = "needs_review"
        with c3:
            if st.button("Reject Document Parse", key=f"reject_document_parse_{intake_id}", width="stretch"):
                result["action"] = "reject"

        with st.expander("Raw Document Parser Data", expanded=False):
            st.json(document_parsed)

    return result
