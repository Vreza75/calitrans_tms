from __future__ import annotations

from urllib.parse import unquote

import pandas as pd
import streamlit as st

from db_client import DispatchDatabaseClient, read_df
from services.customer_status_email_service import _send_customer_status_update_email
from services.dispatch_workflow_service import LOAD_STATUS_FLOW, _load_readiness_details


def render_booking_detail(df: pd.DataFrame, booking: str, refresh_callback=None, port_houston_panel_renderer=None) -> None:
    """Render the booking detail workspace opened from booking query params."""
    booking = unquote(booking)
    if "Booking Number" not in df.columns:
        st.error("Booking Number column is missing from load data.")
        return

    booking_df = df[df["Booking Number"].astype(str).str.strip() == booking.strip()].copy()

    if booking_df.empty:
        st.error("Booking not found.")
        if st.button("Back"):
            st.query_params.clear()
            st.rerun()
        return

    st.title(f"Booking {booking}")
    st.caption("Load timeline, dispatch details, documents, and billing readiness")

    first = booking_df.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customer", str(first.get("Customer", "")))
    c2.metric("Loads", len(booking_df))
    c3.metric("Status", str(first.get("Status", "")))
    c4.metric("Warehouse", str(first.get("Warehouse", "")))

    st.markdown("### Load Details")

    detail_cols = [
        "_row_id",
        "TYPE",
        "Load ID",
        "Container Number",
        "Status",
        "Driver Name",
        "Truck Assigned",
        "Chassis",
        "Delivery Need Date",
        "LFD",
        "Dispatcher Notes",
        "Billing Notes",
    ]
    detail_cols = [column for column in detail_cols if column in booking_df.columns]

    edited = st.data_editor(
        booking_df[detail_cols],
        hide_index=True,
        use_container_width=True,
        disabled=[
            column
            for column in detail_cols
            if column not in ["Status", "Driver Name", "Truck Assigned", "Chassis", "Dispatcher Notes"]
        ],
        column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=LOAD_STATUS_FLOW),
        },
    )

    if st.button("Save Booking Updates"):
        for i in range(len(edited)):
            row_id = int(booking_df.iloc[i]["_row_id"])
            updates = {}
            for column in ["Status", "Driver Name", "Truck Assigned", "Chassis", "Dispatcher Notes"]:
                if column in edited.columns and edited.iloc[i][column] != booking_df.iloc[i][column]:
                    updates[column] = edited.iloc[i][column]
            if updates:
                DispatchDatabaseClient().update_row_fields(row_id, updates)
                if "Status" in updates:
                    _send_customer_status_update_email(
                        row_id,
                        booking_df.iloc[i],
                        str(booking_df.iloc[i].get("Status", "") or ""),
                        str(updates["Status"] or ""),
                        str(updates.get("Dispatcher Notes", booking_df.iloc[i].get("Dispatcher Notes", "")) or ""),
                    )
        if refresh_callback:
            refresh_callback()
        else:
            st.cache_data.clear()
        st.success("Booking updated.")
        st.rerun()

    st.markdown("### Port / PIN / Appointment")
    if callable(port_houston_panel_renderer):
        if len(booking_df) > 1:
            load_options = booking_df["_row_id"].dropna().astype(int).tolist()
            selected_load_id = st.selectbox(
                "Select load for Port/PIN actions",
                load_options,
                format_func=lambda row_id: (
                    f"Row {row_id} | "
                    f"{booking_df[booking_df['_row_id'].astype(int).eq(int(row_id))].iloc[0].get('Container Number', '-')}"
                ),
                key=f"booking_detail_port_pin_load_{booking}",
            )
            selected_load = booking_df[booking_df["_row_id"].astype(int).eq(int(selected_load_id))].iloc[0]
        else:
            selected_load = booking_df.iloc[0]
        readiness = _load_readiness_details(selected_load, include_documents=False)
        try:
            port_houston_panel_renderer(selected_load, readiness)
        except TypeError:
            port_houston_panel_renderer(selected_load)
    else:
        st.info("Port/PIN actions are available from Dispatch Board when Port Houston integration is loaded.")

    st.markdown("### Status Timeline")
    row_ids = booking_df["_row_id"].dropna().astype(int).tolist() if "_row_id" in booking_df.columns else []
    if row_ids:
        timeline = read_df(
            """
            select old_status, new_status, notes, created_by, created_at
            from status_events
            where load_id = any(:ids)
            order by created_at desc
            """,
            {"ids": row_ids},
        )
        st.dataframe(timeline, use_container_width=True, hide_index=True)

    st.markdown("### Documents")
    if row_ids:
        docs = read_df(
            """
            select filename, document_type, file_path, source, created_at
            from documents
            where load_id = any(:ids)
            order by created_at desc
            """,
            {"ids": row_ids},
        )
        st.dataframe(docs, use_container_width=True, hide_index=True)
    else:
        st.info("No load rows found for this booking.")

    if st.button("Back to TMS"):
        st.query_params.clear()
        st.rerun()
