from __future__ import annotations

from urllib.parse import unquote

import pandas as pd
import streamlit as st

from db_client import DispatchDatabaseClient, read_df
from services.customer_status_email_service import _send_customer_status_update_email
from services.dispatch_board_view import get_display_label, get_next_action
from services.dispatch_stages import get_operational_stages
from services.dispatch_transition_service import apply_transition
from services.dispatch_workflow_service import _load_readiness_details


def _run_refresh(refresh_callback) -> None:
    if refresh_callback:
        refresh_callback()
    else:
        st.cache_data.clear()


def _resolve_booking_df(df: pd.DataFrame, booking: str | None, load_id: str | None) -> tuple[pd.DataFrame, str]:
    """Resolve which rows belong in this workspace.

    A ?booking= URL groups every row sharing that booking number. A
    ?load_id= URL (used for cards with no booking number, where the
    booking number can't uniquely key a shared workspace) resolves to that
    one load — unless it turns out to have a booking number after all, in
    which case the whole booking is shown."""
    if load_id:
        try:
            row_id = int(load_id)
        except (TypeError, ValueError):
            return pd.DataFrame(), ""
        if "_row_id" not in df.columns:
            return pd.DataFrame(), ""
        matched = df[df["_row_id"].astype(int).eq(row_id)].copy()
        if matched.empty:
            return matched, ""
        booking_number = str(matched.iloc[0].get("Booking Number", "") or "").strip()
        if booking_number and "Booking Number" in df.columns:
            return df[df["Booking Number"].astype(str).str.strip() == booking_number].copy(), booking_number
        return matched, ""

    if "Booking Number" not in df.columns:
        return pd.DataFrame(), ""
    booking = unquote(booking or "").strip()
    return df[df["Booking Number"].astype(str).str.strip() == booking].copy(), booking


def _render_booking_summary_tab(booking_df: pd.DataFrame, port_houston_panel_renderer) -> None:
    st.markdown("#### Containers in this Booking")
    summary_cols = [
        column
        for column in ["Container Number", "Load ID", "Status", "Driver Name", "Truck Assigned", "Delivery Need Date", "LFD", "Exceptions"]
        if column in booking_df.columns
    ]
    st.dataframe(booking_df[summary_cols], hide_index=True, use_container_width=True)

    st.markdown("#### Port / PIN / Appointment")
    if not callable(port_houston_panel_renderer):
        st.info("Port/PIN actions are available from Dispatch Board when Port Houston integration is loaded.")
        return

    if len(booking_df) > 1:
        load_options = booking_df["_row_id"].dropna().astype(int).tolist()
        selected_load_id = st.selectbox(
            "Select load for Port/PIN actions",
            load_options,
            format_func=lambda row_id: (
                f"Row {row_id} | "
                f"{booking_df[booking_df['_row_id'].astype(int).eq(int(row_id))].iloc[0].get('Container Number', '-')}"
            ),
            key="booking_detail_port_pin_load",
        )
        selected_load = booking_df[booking_df["_row_id"].astype(int).eq(int(selected_load_id))].iloc[0]
    else:
        selected_load = booking_df.iloc[0]

    readiness = _load_readiness_details(selected_load, include_documents=False)
    try:
        port_houston_panel_renderer(selected_load, readiness)
    except TypeError:
        port_houston_panel_renderer(selected_load)


def _render_container_tab(row: pd.Series, refresh_callback) -> None:
    load_id = int(row.get("_row_id", 0) or 0)
    move_type = str(row.get("Dispatch Move Type", "") or row.get("TYPE", "") or "Local Import")
    current_status = str(row.get("Status", "") or "New")
    display_status = get_display_label(move_type, current_status)

    st.markdown(f"**Status:** {display_status}  ·  **Container:** {row.get('Container Number', '') or '-'}")

    detail_cols = st.columns(3)
    detail_cols[0].write(f"**Origin:** {row.get('Port', '') or row.get('Warehouse', '') or '-'}")
    detail_cols[0].write(f"**Destination:** {row.get('Warehouse', '') or row.get('Address', '') or '-'}")
    detail_cols[1].write(f"**Delivery Need Date:** {row.get('Delivery Need Date', '') or '-'}")
    detail_cols[1].write(f"**LFD:** {row.get('LFD', '') or '-'}")
    detail_cols[2].write(f"**Empty Return:** {row.get('empty_return_location', '') or '-'}")
    exceptions = str(row.get("Exceptions", "") or "").strip()
    if exceptions:
        st.warning(f"Exceptions: {exceptions}")

    st.markdown("##### Assignment")
    assign_cols = st.columns(3)
    driver_input = assign_cols[0].text_input("Driver", value=str(row.get("Driver Name", "") or ""), key=f"booking_detail_driver_{load_id}")
    truck_input = assign_cols[1].text_input("Truck", value=str(row.get("Truck Assigned", "") or ""), key=f"booking_detail_truck_{load_id}")
    chassis_input = assign_cols[2].text_input("Chassis", value=str(row.get("Chassis", "") or ""), key=f"booking_detail_chassis_{load_id}")
    notes_input = st.text_area("Dispatcher Notes", value=str(row.get("Dispatcher Notes", "") or ""), key=f"booking_detail_notes_{load_id}")

    if st.button("Save Assignment / Notes", key=f"booking_detail_save_{load_id}"):
        result = apply_transition(load_id, current_status, driver=driver_input, truck=truck_input)
        if not result["ok"]:
            st.error(result["reason"])
        else:
            field_updates = {}
            if chassis_input.strip() != str(row.get("Chassis", "") or "").strip():
                field_updates["Chassis"] = chassis_input.strip()
            if notes_input.strip() != str(row.get("Dispatcher Notes", "") or "").strip():
                field_updates["Dispatcher Notes"] = notes_input.strip()
            if field_updates:
                DispatchDatabaseClient().update_row_fields(load_id, field_updates)
            _run_refresh(refresh_callback)
            st.success("Saved.")
            st.rerun()

    st.markdown("##### Status Transition")
    has_driver = bool(driver_input.strip())
    empty_return_required = bool(str(row.get("empty_return_location", "") or "").strip())
    next_action = get_next_action(move_type, current_status, has_driver=has_driver, empty_return_required=empty_return_required)
    if next_action:
        label, target_status = next_action
        if st.button(label, key=f"booking_detail_next_action_{load_id}"):
            result = apply_transition(load_id, target_status, driver=driver_input, truck=truck_input, note=notes_input)
            if not result["ok"]:
                st.error(result["reason"])
            else:
                _send_customer_status_update_email(load_id, row, current_status, target_status, notes_input)
                _run_refresh(refresh_callback)
                st.success(f"Moved to {get_display_label(move_type, target_status)}.")
                st.rerun()
    else:
        st.caption("No further operational action from this status.")

    with st.expander("Manual status override"):
        stages = get_operational_stages(move_type)
        default_index = stages.index(current_status) if current_status in stages else 0
        override_target = st.selectbox("Set status to", stages, index=default_index, key=f"booking_detail_override_target_{load_id}")
        override_reason = st.text_input("Override reason (required)", key=f"booking_detail_override_reason_{load_id}")
        if st.button("Apply Override", key=f"booking_detail_override_apply_{load_id}"):
            if not override_reason.strip():
                st.error("An override requires a reason.")
            else:
                result = apply_transition(
                    load_id, override_target, driver=driver_input, truck=truck_input,
                    override=True, override_reason=override_reason,
                )
                if not result["ok"]:
                    st.error(result["reason"])
                else:
                    _send_customer_status_update_email(load_id, row, current_status, override_target, override_reason)
                    _run_refresh(refresh_callback)
                    st.success(f"Status overridden to {get_display_label(move_type, override_target)}.")
                    st.rerun()


def render_booking_detail(
    df: pd.DataFrame,
    booking: str | None,
    load_id: str | None = None,
    refresh_callback=None,
    port_houston_panel_renderer=None,
) -> None:
    """Render the booking workspace opened from a ?booking= or ?load_id= query param."""
    booking_df, booking_label = _resolve_booking_df(df, booking, load_id)

    if booking_df.empty:
        st.error("Booking not found.")
        if st.button("Back"):
            st.query_params.clear()
            st.rerun()
        return

    first = booking_df.iloc[0]
    st.title(f"Booking {booking_label}" if booking_label else f"Load {first.get('Load ID', '')}")
    st.caption("Booking workspace — dispatch status, assignment, documents, and billing readiness")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customer", str(first.get("Customer", "")))
    c2.metric("Containers", len(booking_df))
    c3.metric("Move Type", str(first.get("Dispatch Move Type", "") or first.get("TYPE", "")))
    unassigned_mask = booking_df.get("Driver Name", pd.Series(dtype=str)).astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])
    c4.metric("Unassigned", int(unassigned_mask.sum()))

    row_ids = booking_df["_row_id"].dropna().astype(int).tolist() if "_row_id" in booking_df.columns else []

    if len(booking_df) > 1:
        tab_labels = ["Booking Summary"] + [
            f"Container {i + 1} — {str(row.get('Container Number', '') or row.get('Load ID', '') or row.get('_row_id', ''))}"
            for i, (_, row) in enumerate(booking_df.iterrows())
        ]
        tabs = st.tabs(tab_labels)
        with tabs[0]:
            _render_booking_summary_tab(booking_df, port_houston_panel_renderer)
        for i, (_, row) in enumerate(booking_df.iterrows()):
            with tabs[i + 1]:
                _render_container_tab(row, refresh_callback)
    else:
        _render_booking_summary_tab(booking_df, port_houston_panel_renderer)
        st.markdown("### Container Details")
        _render_container_tab(booking_df.iloc[0], refresh_callback)

    st.markdown("### Status Timeline")
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
