from __future__ import annotations

import pandas as pd
import streamlit as st

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, has_permission
from application.documents.commands import attach_load_document
from application.exceptions import AuthorizationError
from db_client import DispatchDatabaseClient, read_df
from services.order_parser import extract_text_from_pdf, parse_order_text
from services.tms_data_service import refresh_data as _refresh_tms_data
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter


def _normalize_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def render_documents(df: pd.DataFrame, principal: AuthenticatedActor) -> None:
    """Render the document repository and attach-file workflow."""
    st.subheader("Documents")

    try:
        docs = read_df(
            """
            select
                d.id,
                l.type as service_flow,
                l.booking_number,
                l.container_number,
                d.document_type,
                d.filename,
                d.status,
                d.source,
                d.created_at
            from documents d
            left join loads l on l.id = d.load_id
            order by d.created_at desc
            """
        )
    except Exception as exc:
        st.error(f"Could not load documents: {exc}")
        return

    selected_flow = render_service_flow_filter("documents_service_flow")
    docs = apply_service_flow_filter(docs, selected_flow, column="service_flow")
    st.dataframe(docs, use_container_width=True, hide_index=True)

    with st.expander("Upload document to load", expanded=False):
        load_df = apply_service_flow_filter(df, selected_flow)
        if load_df.empty or "_row_id" not in load_df.columns:
            st.info("No loads are available for document attachment.")
            return

        labels = [
            f"{row.get('Booking Number', '')} | {row.get('Container Number', '')} | row {int(row['_row_id'])}"
            for _, row in load_df.dropna(subset=["_row_id"]).iterrows()
        ]
        if not labels:
            st.info("No valid load rows are available for document attachment.")
            return

        selected = st.selectbox("Select load", labels)
        row_id = int(selected.split("row ")[-1])
        doc_type = st.selectbox("Document Type", ["load_order", "rate_confirmation", "bol", "pod", "invoice", "other"])
        uploaded = st.file_uploader("Upload PDF or image", type=["pdf", "png", "jpg", "jpeg"])

        can_attach = has_permission(principal, Permission.DOCUMENT_ATTACH)
        if not can_attach:
            st.caption("Your role does not have permission to attach documents.")

        if st.button("Attach Document", disabled=not can_attach) and uploaded is not None:
            try:
                attach_load_document(actor=principal, load_id=row_id, uploaded_file=uploaded, source=doc_type)
            except AuthorizationError as exc:
                st.error(str(exc))
            else:
                st.success("Document received - finalizing storage.")
                st.rerun()


def render_pdf_intake(refresh_callback=None) -> None:
    """Render the legacy PDF order intake workflow."""
    st.subheader("PDF / Order Intake")

    uploaded_file = st.file_uploader("Upload load order PDF", type=["pdf"])

    if uploaded_file is None:
        return

    pdf_text = extract_text_from_pdf(uploaded_file)
    parsed = parse_order_text(pdf_text)

    st.markdown("### Parsed Order")
    st.json(parsed)

    if st.button("Create Load From Parsed PDF"):
        created = DispatchDatabaseClient().add_row(
            {
                "TYPE": parsed.get("TYPE", "Import"),
                "Booking Number": parsed.get("Booking Number", ""),
                "Reference Number": parsed.get("Reference Number", ""),
                "Customer": parsed.get("Customer", ""),
                "Container Number": parsed.get("Container Number", ""),
                "Port": parsed.get("Port", ""),
                "Warehouse": parsed.get("Warehouse", ""),
                "Document Cutoff": _normalize_date(parsed.get("Document Cutoff", "")),
                "Delivery Need Date": _normalize_date(parsed.get("Delivery Need Date", "")),
                "Status": parsed.get("Status", "New") or "New",
                "Dispatcher Notes": parsed.get("Dispatcher Notes", ""),
            }
        )
        DispatchDatabaseClient().attach_file_to_row(created.id, uploaded_file, source="pdf_intake")
        if refresh_callback:
            refresh_callback()
        else:
            # This creates a load row - only tms_data_service's load
            # caches are affected, so this targets those rather than
            # clearing every cache app-wide.
            _refresh_tms_data()
        st.success(f"Created load ID {created.id}")
