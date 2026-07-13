from __future__ import annotations

import re
from datetime import date
from html import escape
from typing import Callable

import pandas as pd
import streamlit as st

from db_client import DispatchDatabaseClient
from services.dispatch_data_service import (
    _insert_dispatch_message,
    _read_dispatch_messages,
    _read_documents_for_load,
    _read_status_timeline,
    _save_status_quick_update,
    _update_load_extra_fields,
)
from services.dispatch_workflow_service import (
    DISPATCH_BOARD_STATUSES,
    LOAD_STATUS_FLOW,
    LOAD_TYPE_TABS,
    _clean_display_value,
    _generate_driver_dispatch_message,
    _get_status_border_color,
    _get_status_color,
    _int_or_none,
    _load_exception_summary,
    _load_readiness_details,
    _normalize_load_type,
    _normalize_load_type_value,
    _safe_str,
    _status_row_style,
)
from services.customer_status_email_service import _send_customer_status_update_email
from services.dispatch_board_view import (
    get_board_columns,
    get_display_label,
    get_next_action,
    is_active_dispatch_status,
)
from services.dispatch_card_priority import sort_booking_cards
from services.dispatch_card_view_model import build_booking_card_view_models
from services.dispatch_stages import CANCELLED_STATUS, get_operational_stages
from services.dispatch_transition_service import apply_transition
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter
from ui_components.status_badge import render_status_badge


def _run_refresh(refresh_callback: Callable[[], None] | None = None) -> None:
    if callable(refresh_callback):
        refresh_callback()
    else:
        try:
            st.cache_data.clear()
        except Exception:
            pass


def _render_port_panel(selected_load, readiness: dict | None = None, port_houston_panel_renderer: Callable | None = None) -> None:
    if callable(port_houston_panel_renderer):
        try:
            port_houston_panel_renderer(selected_load, readiness or {})
        except TypeError:
            port_houston_panel_renderer(selected_load)
    else:
        st.info("Port Houston panel is not available from this page context.")

def render_load_card(row) -> None:
    booking = str(row.get("Booking Number", "") or "")
    row_id = int(row.get("_row_id", 0))
    status = str(row.get("Status", "") or "")
    container = str(row.get("Container Number", "") or "-")
    customer = str(row.get("Customer", "") or "-")
    driver = _clean_display_value(row.get("Driver Name", ""), "Unassigned")
    need_date = str(row.get("Delivery Need Date", "") or "-")

    status_color = _get_status_color(status)
    border_color = _get_status_border_color(status)

    st.markdown(
        f"""
        <div style="
            background:{status_color};
            border-left:5px solid {border_color};
            border-radius:8px;
            padding:6px 7px;
            margin-bottom:5px;
            font-size:10px;
            line-height:1.15;
        ">
            <b>{booking}</b><br>
            {container}<br>
            <span>{customer}</span><br>
            <span>{driver}</span> · <span>{need_date}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("Open", key=f"open_load_{row_id}", use_container_width=True):
        st.session_state["selected_dispatch_load_id"] = row_id
        st.session_state["show_load_workspace_dialog"] = True
        st.rerun()

def _get_selected_dispatch_load(df: pd.DataFrame):
    selected_id = st.session_state.get("selected_dispatch_load_id")

    if selected_id is None and not df.empty:
        selected_id = int(df.iloc[0]["_row_id"])
        st.session_state["selected_dispatch_load_id"] = selected_id

    if selected_id is None:
        return None

    selected_df = df[df["_row_id"].astype(int).eq(int(selected_id))]
    if selected_df.empty:
        return None

    return selected_df.iloc[0]


def _render_operational_status_tab(selected_load, load_id: int, current_status: str, operational_stages: list[str], refresh_callback) -> None:
    status_options = operational_stages + [CANCELLED_STATUS]
    current_index = status_options.index(current_status) if current_status in status_options else 0

    c1, c2, c3, c4 = st.columns(4)
    new_status = c1.selectbox("New Status", status_options, index=current_index, key=f"new_status_{load_id}")
    driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""), key=f"status_driver_{load_id}")
    truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""), key=f"status_truck_{load_id}")
    chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""), key=f"status_chassis_{load_id}")
    customer_email = st.text_input(
        "Customer Email",
        value=str(selected_load.get("Customer Email", "") or ""),
        key=f"customer_email_{load_id}",
    )
    note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120, key=f"status_note_{load_id}")

    override = st.checkbox("Override transition rules (requires a reason)", key=f"status_override_{load_id}")
    override_reason = ""
    if override:
        override_reason = st.text_input("Override reason", key=f"status_override_reason_{load_id}")

    if st.button("Save Status Update", key=f"save_status_{load_id}"):
        detail_updates = {}
        if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
            detail_updates["Driver Name"] = driver.strip()
        if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
            detail_updates["Truck Assigned"] = truck.strip()
        if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
            detail_updates["Chassis"] = chassis.strip()

        if detail_updates:
            DispatchDatabaseClient().update_row_fields(load_id, detail_updates)

        if new_status != current_status:
            result = apply_transition(
                load_id,
                new_status,
                note=note.strip(),
                override=override,
                override_reason=override_reason.strip(),
            )
            if not result["ok"]:
                st.error(result["reason"])
                return

            email_sent, email_msg = _send_customer_status_update_email(
                load_id, selected_load, current_status, new_status, note.strip(), customer_email.strip(),
            )
            if email_sent:
                st.success(f"Status updated. {email_msg}")
            else:
                st.warning(f"Status updated, but customer email was not sent: {email_msg}")

            _run_refresh(refresh_callback)
            st.rerun()
        elif detail_updates:
            st.success("Load details updated.")
            _run_refresh(refresh_callback)
            st.rerun()
        else:
            st.info("No changes detected.")


def _render_legacy_status_tab(selected_load, load_id: int, current_status: str, refresh_callback) -> None:
    """Unchanged pre-dispatch status path — loads not yet in the new
    operational model (Ready to Dispatch or later) keep the original free
    status selectbox until Phase 5 gives them their own Intake &
    Verification workspace."""
    c1, c2, c3, c4 = st.columns(4)
    status_index = LOAD_STATUS_FLOW.index(current_status) if current_status in LOAD_STATUS_FLOW else 0

    new_status = c1.selectbox("New Status", LOAD_STATUS_FLOW, index=status_index, key=f"legacy_status_{load_id}")
    driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""), key=f"legacy_driver_{load_id}")
    truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""), key=f"legacy_truck_{load_id}")
    chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""), key=f"legacy_chassis_{load_id}")
    customer_email = st.text_input(
        "Customer Email",
        value=str(selected_load.get("Customer Email", "") or ""),
        key=f"legacy_customer_email_{load_id}",
    )

    note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120, key=f"legacy_note_{load_id}")

    if st.button("Save Status Update", key=f"legacy_save_status_{load_id}"):
        readiness = _load_readiness_details(selected_load, documents_df=_read_documents_for_load(load_id))
        if (
            new_status in ["Ready to Dispatch", "Dispatched"]
            and new_status != current_status
            and not readiness.get("dispatchable")
        ):
            st.error("This load cannot be marked Ready to Dispatch or Dispatched until order details, port verification, driver, truck, and PIN/appointment are complete.")
            return
        updates = {}
        if new_status != current_status:
            updates["Status"] = new_status
        if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
            updates["Driver Name"] = driver.strip()
        if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
            updates["Truck Assigned"] = truck.strip()
        if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
            updates["Chassis"] = chassis.strip()
        if note.strip() != str(selected_load.get("Dispatcher Notes", "") or "").strip():
            updates["Dispatcher Notes"] = note.strip()

        if updates:
            DispatchDatabaseClient().update_row_fields(load_id, updates)

            if "Status" in updates:
                email_sent, email_msg = _send_customer_status_update_email(
                    load_id, selected_load, current_status, new_status, note.strip(), customer_email.strip(),
                )
                if email_sent:
                    st.success(f"Status updated. {email_msg}")
                else:
                    st.warning(f"Status updated, but customer email was not sent: {email_msg}")
            else:
                st.success("Load details updated.")

            _run_refresh(refresh_callback)
            st.rerun()
        else:
            st.info("No changes detected.")


def render_dispatch_workspace(selected_load, refresh_callback: Callable[[], None] | None = None, port_houston_panel_renderer: Callable | None = None) -> None:
    load_id = int(selected_load["_row_id"])
    booking = str(selected_load.get("Booking Number", "") or "")
    container = str(selected_load.get("Container Number", "") or "-")
    customer = str(selected_load.get("Customer", "") or "-")
    load_documents_df = _read_documents_for_load(load_id)
    readiness = _load_readiness_details(selected_load, documents_df=load_documents_df)

    st.markdown("---")
    st.markdown(f"## Load Workspace: {booking}")
    st.caption(f"{customer} · Container {container}")

    top = st.columns(6)
    top[0].metric("Status", str(selected_load.get("Status", "") or "-"))
    top[1].metric("Readiness", f"{readiness['score']}%")
    top[2].metric("Next Action", readiness["next_action"])
    top[3].metric("Driver", str(selected_load.get("Driver Name", "") or "Unassigned"))
    top[4].metric("Truck", str(selected_load.get("Truck Assigned", "") or "-"))
    top[5].metric("LFD", str(selected_load.get("LFD", "") or "-"))

    if readiness["missing"]:
        st.warning("Missing before dispatch: " + ", ".join(readiness["missing"]))
    else:
        st.success("Load readiness checklist is complete.")
    if readiness["exceptions"]:
        st.error("Exceptions: " + ", ".join(readiness["exceptions"]))

    dispatch_tab, port_tab, status_tab, timeline_tab, driver_tab, customer_tab, docs_tab, billing_tab = st.tabs(
        ["Dispatch Details", "Port Sync / PIN", "Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Documents", "Billing"]
    )

    with dispatch_tab:
        st.markdown("### Dispatch Progress Details")
        c1, c2 = st.columns(2)

        with c1:
            st.write("**Start / Pickup Point**")
            st.info(str(selected_load.get("Port", "") or "Not set"))
            st.write("**Delivery / Final Point**")
            st.info(str(selected_load.get("Warehouse", "") or "Not set"))
            st.write("**Address**")
            st.info(str(selected_load.get("Address", "") or "Not set"))

        with c2:
            current_location = st.text_input(
                "Current Location",
                value=str(selected_load.get("current_location", "") or ""),
                placeholder="Example: Bayport Terminal, I-10 East, Baytown DC...",
                key=f"current_location_{load_id}",
            )

            eta_date = st.date_input("ETA Date", value=None, key=f"eta_date_{load_id}")
            eta_time = st.time_input("ETA Time", value=None, key=f"eta_time_{load_id}")

            live_load_status = st.selectbox(
                "Live Load Status",
                ["", "Not Started", "Waiting", "In Progress", "Completed", "Issue / Delay"],
                index=0,
                key=f"live_load_{load_id}",
            )

            live_unload_status = st.selectbox(
                "Live Unload Status",
                ["", "Not Started", "Waiting", "In Progress", "Completed", "Issue / Delay"],
                index=0,
                key=f"live_unload_{load_id}",
            )

        eta_value = None
        if eta_date and eta_time:
            eta_value = pd.Timestamp.combine(eta_date, eta_time).to_pydatetime()

        if st.button("Save Dispatch Progress", key=f"save_dispatch_progress_{load_id}"):
            _update_load_extra_fields(load_id, current_location, eta_value, live_load_status, live_unload_status)
            st.success("Dispatch progress saved.")
            _run_refresh(refresh_callback)
            st.rerun()

    with port_tab:
        _render_port_panel(selected_load, readiness, port_houston_panel_renderer)

    with status_tab:
        st.markdown("### Status Update")
        current_status = str(selected_load.get("Status", "") or "New")
        move_type = _normalize_load_type(selected_load)
        operational_stages = get_operational_stages(move_type)

        if current_status in operational_stages or current_status == CANCELLED_STATUS:
            _render_operational_status_tab(
                selected_load, load_id, current_status, operational_stages, refresh_callback
            )
        else:
            _render_legacy_status_tab(selected_load, load_id, current_status, refresh_callback)

    with timeline_tab:
        st.markdown("### Load Timeline")
        timeline = _read_status_timeline(load_id)
        if timeline.empty:
            st.info("No timeline records yet.")
        else:
            st.dataframe(timeline, use_container_width=True, hide_index=True)

    with driver_tab:
        st.markdown("### Driver Communication Center")
        st.caption(
            "Generate dispatch instructions, save driver messages, and record quick driver status updates. "
            "SMS/Motive sending can be connected later through FastAPI."
        )

        load_id = int(selected_load["_row_id"])
        current_status = _clean_display_value(selected_load.get("Status", ""), "New")
        driver_name = _clean_display_value(selected_load.get("Driver Name", ""), "Unassigned")
        truck = _clean_display_value(selected_load.get("Truck Assigned", ""), "-")
        chassis = _clean_display_value(selected_load.get("Chassis", ""), "-")
        booking = _clean_display_value(selected_load.get("Booking Number", ""), "-")
        container = _clean_display_value(selected_load.get("Container Number", ""), "-")

        st.markdown("#### Driver Assignment")
        info_cols = st.columns(5)
        info_cols[0].metric("Driver", driver_name)
        info_cols[1].metric("Truck", truck)
        info_cols[2].metric("Chassis", chassis)
        info_cols[3].metric("Status", current_status)
        info_cols[4].metric("Container", container)

        st.markdown("#### Generated Dispatch Message")

        generated_message = _generate_driver_dispatch_message(selected_load)
        packet_ready = bool(readiness.get("dispatchable"))
        if not packet_ready:
            st.warning("Driver packet is locked until customer/order, port verification, driver, truck, and PIN/appointment are complete.")

        edited_message = st.text_area(
            "Dispatch Message",
            value=generated_message,
            height=260,
            key=f"generated_dispatch_msg_{load_id}",
        )

        action_cols = st.columns(4)

        with action_cols[0]:
            if st.button(
                "Save Message",
                key=f"save_generated_driver_msg_{load_id}",
                use_container_width=True,
                disabled=not packet_ready,
            ):
                _insert_dispatch_message(
                    load_id,
                    "driver_dispatch_message",
                    "outbound",
                    driver_name,
                    edited_message.strip(),
                )
                st.success("Driver dispatch message saved to history.")
                st.rerun()

        with action_cols[1]:
            st.download_button(
                "Download Message",
                data=edited_message,
                file_name=f"dispatch_message_{booking}.txt",
                mime="text/plain",
                key=f"download_dispatch_msg_{load_id}",
                use_container_width=True,
                disabled=not packet_ready,
            )

        with action_cols[2]:
            if st.button(
                "Copy/Paste Ready",
                key=f"copy_ready_{load_id}",
                use_container_width=True,
                disabled=not packet_ready,
            ):
                _insert_dispatch_message(
                    load_id,
                    "driver_dispatch_message_copy_ready",
                    "outbound",
                    driver_name,
                    edited_message.strip(),
                )
                st.info("Message saved. Copy the text above and paste into Motive.")

        with action_cols[3]:
            st.button(
                "Send via Motive",
                key=f"send_motive_placeholder_{load_id}",
                disabled=True,
                use_container_width=True,
                help="Future FastAPI + Motive integration",
            )

        st.markdown("#### Quick Driver Status Updates")
        st.caption("These buttons update load status and create a communication log entry.")

        move_type = _normalize_load_type(selected_load)
        if move_type == "Import":
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to port/terminal."),
                ("At Port", "Driver arrived at terminal."),
                ("Loaded", "Container picked up and loaded."),
                ("En Route To Delivery", "Driver en route to warehouse/customer."),
                ("Delivered", "Delivery completed. Awaiting POD if not received."),
                ("Returning Empty", "Driver returning empty container/chassis."),
                ("POD Received", "POD received and saved for billing."),
            ]
        elif move_type == "Export":
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to empty yard or shipper."),
                ("At Pickup", "Driver arrived at empty yard or shipper."),
                ("Loaded", "Export load picked up and loaded."),
                ("En Route To Delivery", "Driver en route to port."),
                ("At Port", "Driver arrived at terminal."),
                ("Delivered", "Export delivered to port. Awaiting POD if not received."),
                ("POD Received", "POD received and saved for billing."),
            ]
        else:
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to pickup."),
                ("At Pickup", "Driver arrived at pickup."),
                ("Loaded", "Load picked up and loaded."),
                ("En Route To Delivery", "Driver en route to delivery."),
                ("Delivered", "Delivery completed. Awaiting POD if not received."),
                ("POD Received", "POD received and saved for billing."),
            ]

        quick_cols = st.columns(4)
        for idx, (status_label, default_note) in enumerate(quick_statuses):
            with quick_cols[idx % 4]:
                if st.button(status_label, key=f"quick_status_{load_id}_{status_label}", use_container_width=True):
                    email_sent, email_msg = _save_status_quick_update(load_id, selected_load, status_label, default_note)
                    _run_refresh(refresh_callback)
                    if email_sent:
                        st.success(f"Updated to {status_label}. {email_msg}")
                    else:
                        st.warning(f"Updated to {status_label}, but customer email was not sent: {email_msg}")
                    st.rerun()

        st.markdown("#### Manual Driver Note / Message")
        manual_cols = st.columns([1, 2])
        recipient = manual_cols[0].text_input(
            "Driver / Phone",
            value=driver_name,
            key=f"driver_recipient_{load_id}",
        )
        message_body = manual_cols[1].text_area(
            "Message / Note",
            placeholder="Example: Confirm container released. Send ETA when loaded.",
            height=120,
            key=f"manual_driver_msg_{load_id}",
        )

        msg_cols = st.columns(3)
        with msg_cols[0]:
            message_type = st.selectbox(
                "Message Type",
                ["driver_note", "driver_message", "driver_reply_log", "motive_message_log"],
                key=f"driver_msg_type_{load_id}",
            )

        with msg_cols[1]:
            direction = st.selectbox(
                "Direction",
                ["outbound", "inbound", "internal"],
                key=f"driver_msg_direction_{load_id}",
            )

        with msg_cols[2]:
            st.write("")
            st.write("")
            save_manual = st.button("Save Driver Communication", key=f"save_manual_driver_msg_{load_id}")

        if save_manual:
            if not message_body.strip():
                st.error("Message is required.")
            else:
                _insert_dispatch_message(
                    load_id,
                    message_type,
                    direction,
                    recipient,
                    message_body.strip(),
                )
                st.success("Driver communication saved.")
                st.rerun()

        st.markdown("#### Driver Communication Thread")
        messages = _read_dispatch_messages(load_id)

        if messages.empty:
            st.info("No driver messages have been saved yet.")
        else:
            driver_messages = messages[
                messages["message_type"].astype(str).str.contains("driver|motive", case=False, na=False)
            ].copy()

            if driver_messages.empty:
                st.info("No driver-specific messages have been saved yet.")
            else:
                display_cols = [
                    "created_at",
                    "direction",
                    "message_type",
                    "recipient",
                    "message_body",
                    "sent_by",
                ]
                display_cols = [c for c in display_cols if c in driver_messages.columns]
                st.dataframe(driver_messages[display_cols], use_container_width=True, hide_index=True)

    with customer_tab:
        st.markdown("### Customer Notes / Updates")
        customer_note = st.text_area("Customer Update Note", placeholder="Example: Container picked up. ETA to warehouse 2:30 PM.", height=100)
        if st.button("Save Customer Note", key=f"save_customer_note_{load_id}"):
            if not customer_note.strip():
                st.error("Customer note is required.")
            else:
                _insert_dispatch_message(load_id, "customer_note", "outbound", customer, customer_note.strip())
                st.success("Customer note saved.")
                st.rerun()

        messages = _read_dispatch_messages(load_id)
        customer_messages = messages[messages["message_type"].astype(str).str.contains("customer", case=False, na=False)] if not messages.empty else pd.DataFrame()
        st.dataframe(customer_messages, use_container_width=True, hide_index=True)

    with docs_tab:
        st.markdown("### Documents")
        docs = _read_documents_for_load(load_id)
        st.dataframe(docs, use_container_width=True, hide_index=True)
        uploaded = st.file_uploader("Attach document to this load", type=["pdf", "png", "jpg", "jpeg"], key=f"doc_upload_{load_id}")
        if st.button("Attach Document", key=f"attach_doc_{load_id}") and uploaded is not None:
            DispatchDatabaseClient().attach_file_to_row(load_id, uploaded, source="dispatch_workspace")
            st.success("Document attached.")
            st.rerun()

    with billing_tab:
        st.markdown("### Billing Readiness")
        st.write("**Billing Notes**")
        st.info(str(selected_load.get("Billing Notes", "") or "No billing notes."))
        billing_status = str(selected_load.get("Status", "") or "")
        if billing_status in ["POD Received", "Ready for ProfitTools", "Exported to ProfitTools", "Invoiced", "Closed"]:
            st.success("This load is in the billing workflow.")
        else:
            st.warning("This load is not ready for billing yet.")

        if st.button("Mark Ready for ProfitTools", key=f"mark_billing_{load_id}"):
            old_status = str(selected_load.get("Status", "") or "")
            new_status = "Ready for ProfitTools"
            DispatchDatabaseClient().update_row_fields(load_id, {"Status": new_status})
            email_sent, email_msg = _send_customer_status_update_email(
                load_id,
                selected_load,
                old_status,
                new_status,
                "Load is ready for billing/export review.",
            )
            if email_sent:
                st.success(f"Marked Ready for ProfitTools. {email_msg}")
            else:
                st.warning(f"Marked Ready for ProfitTools, but customer email was not sent: {email_msg}")
            _run_refresh(refresh_callback)
            st.rerun()

def open_load_workspace_dialog(selected_load):
    render_dispatch_workspace(selected_load, refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)

def render_dispatch_board(df: pd.DataFrame, refresh_callback: Callable[[], None] | None = None, port_houston_panel_renderer: Callable | None = None) -> None:
    st.subheader("Dispatch Board")
    st.caption("Live Dispatch, Tomorrow Planning, and Future Pipeline.")

    board_df = df.copy()
    board_df["TYPE"] = board_df.get("TYPE", pd.Series("", index=board_df.index)).apply(_normalize_load_type_value)
    selected_flow = render_service_flow_filter("dispatch_board_legacy_service_flow")
    board_df = apply_service_flow_filter(board_df, selected_flow)

    board_df["Delivery Date Parsed"] = pd.to_datetime(
        board_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce",
    )

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    live_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(today)
        & board_df["Status"].isin(DISPATCH_BOARD_STATUSES)
    ].copy()

    tomorrow_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()

    future_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()

    main_tabs = st.tabs(["Live Dispatch", "Tomorrow Planning", "Future Pipeline"])

    with main_tabs[0]:
        st.markdown("### Live Dispatch")

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = live_df[
                    live_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value}")
                st.caption(f"{len(type_df)} active load(s) today")

                status_cols = st.columns(len(DISPATCH_BOARD_STATUSES), gap="small")

                for idx, status in enumerate(DISPATCH_BOARD_STATUSES):
                    with status_cols[idx]:
                        status_df = type_df[
                            type_df["Status"].astype(str).str.strip().eq(status)
                        ].copy()

                        st.markdown(
                            f"""
                            <div style="
                                text-align:center;
                                font-weight:800;
                                background:#f1f5f9;
                                border:1px solid #cbd5e1;
                                border-radius:10px;
                                padding:8px;
                                margin-bottom:8px;
                            ">
                                {status}<br>
                                <span style="font-size:18px;">{len(status_df)}</span>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if status_df.empty:
                            st.caption("No loads")
                        else:
                            for _, row in status_df.head(30).iterrows():
                                render_load_card(row)

    with main_tabs[1]:
        st.markdown("### Tomorrow Planning")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tomorrow Loads", len(tomorrow_df))
        k2.metric("Assigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().ne("").sum()))
        k3.metric("Unassigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().isin(["", "nan", "None", "Unassigned"]).sum()))
        k4.metric("Needs Info", int(tomorrow_df["Status"].eq("Hold/Need Info").sum()))

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = tomorrow_df[
                    tomorrow_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value} — Tomorrow")
                st.caption(f"{len(type_df)} planned load(s)")

                if type_df.empty:
                    st.info(f"No {type_value} loads planned for tomorrow.")
                    continue

                columns = [
                    "_row_id",
                    "TYPE",
                    "Booking Number",
                    "Load ID",
                    "Customer",
                    "Container Number",
                    "Warehouse",
                    "Delivery Need Date",
                    "LFD",
                    "Status",
                    "Driver Name",
                    "Truck Assigned",
                    "Chassis",
                    "Dispatcher Notes",
                ]

                display_cols = [c for c in columns if c in type_df.columns]

                styled = (
                    type_df.sort_values(["Status", "Delivery Need Date"], ascending=[True, True])[display_cols]
                    .style
                    .apply(_status_row_style, axis=1)
                )

                st.dataframe(styled, use_container_width=True, hide_index=True)

    with main_tabs[2]:
        st.markdown("### Future Pipeline")

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = future_df[
                    future_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value} — Future")
                st.caption(f"{len(type_df)} upcoming load(s)")

                if type_df.empty:
                    st.info(f"No future {type_value} loads found.")
                    continue

                columns = [
                    "_row_id",
                    "TYPE",
                    "Booking Number",
                    "Load ID",
                    "Customer",
                    "Container Number",
                    "Port",
                    "Warehouse",
                    "Delivery Need Date",
                    "LFD",
                    "Status",
                    "Driver Name",
                    "Dispatcher Notes",
                ]

                display_cols = [c for c in columns if c in type_df.columns]

                st.dataframe(
                    type_df.sort_values("Delivery Need Date")[display_cols],
                    use_container_width=True,
                    hide_index=True,
                )

    if st.session_state.get("show_load_workspace_dialog"):
        selected_load = _get_selected_dispatch_load(df)

        if selected_load is not None:
            open_load_workspace_dialog(selected_load)

def _risk_level(row) -> str:
    """"" (healthy), "risk" (approaching/unassigned), or "late" (severe)."""
    status = str(row.get("Status", "") or "")
    if status in ("Completed", "Cancelled"):
        return ""
    delivery_date = pd.to_datetime(row.get("Delivery Need Date", ""), errors="coerce")
    today = pd.Timestamp(pd.Timestamp.now().date())
    if pd.notna(delivery_date) and delivery_date.normalize() < today:
        return "late"
    exceptions = _safe_str(row.get("Exceptions", ""))
    if exceptions:
        return "risk"
    if status == "Ready to Dispatch" and not str(row.get("Driver Name", "") or "").strip():
        return "risk"
    return ""


_RISK_BADGE = {
    "late": '<span style="background:#fee2e2;color:#991b1b;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">Late</span>',
    "risk": '<span style="background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">Risk</span>',
    "": "",
}


def _render_row_next_action(row, load_id: int, move_type: str, canonical_status: str, empty_return_required: bool, refresh_callback) -> None:
    has_driver = bool(str(row.get("Driver Name", "") or "").strip())
    action = get_next_action(move_type, canonical_status, has_driver=has_driver, empty_return_required=empty_return_required)

    if action is None:
        st.caption("—")
        return

    label, target_status = action
    needs_assignment = canonical_status == "Ready to Dispatch" and not has_driver

    if needs_assignment:
        assign_key = f"assign_open_{load_id}"
        if st.session_state.get(assign_key):
            driver_input = st.text_input("Driver", key=f"assign_driver_{load_id}", label_visibility="collapsed", placeholder="Driver name")
            truck_input = st.text_input("Truck", key=f"assign_truck_{load_id}", label_visibility="collapsed", placeholder="Truck")
            if st.button("Confirm", key=f"assign_confirm_{load_id}", use_container_width=True):
                if not driver_input.strip():
                    st.error("Driver is required.")
                else:
                    result = apply_transition(load_id, target_status, driver=driver_input.strip(), truck=truck_input.strip())
                    if not result["ok"]:
                        st.error(result["reason"])
                    else:
                        st.session_state.pop(assign_key, None)
                        _run_refresh(refresh_callback)
                        st.rerun()
        else:
            if st.button(label, key=f"next_action_{load_id}", use_container_width=True):
                st.session_state[assign_key] = True
                st.rerun()
        return

    if st.button(label, key=f"next_action_{load_id}", use_container_width=True):
        result = apply_transition(load_id, target_status)
        if not result["ok"]:
            st.error(result["reason"])
        else:
            _run_refresh(refresh_callback)
            st.rerun()


def _render_dispatch_row(row, refresh_callback) -> None:
    load_id = _int_or_none(row.get("_row_id")) or 0
    move_type = _clean_display_value(row.get("Dispatch Move Type", ""), _normalize_load_type(row))
    canonical_status = _clean_display_value(row.get("Status", ""), "New")
    empty_return_required = bool(str(row.get("empty_return_location", "") or "").strip())
    display_status = get_display_label(move_type, canonical_status)

    booking = _clean_display_value(row.get("Booking Number", ""), "-")
    container = _clean_display_value(row.get("Container Number", ""), "-")
    customer = _clean_display_value(row.get("Customer", ""), "-")
    origin = _clean_display_value(row.get("Port", "") or row.get("Warehouse", ""), "-")
    destination = _clean_display_value(row.get("Warehouse", "") or row.get("Address", ""), "-")
    need_date = _clean_display_value(row.get("Delivery Need Date", ""), "-")
    lfd = _clean_display_value(row.get("LFD", ""), "-")
    driver = _clean_display_value(row.get("Driver Name", ""), "Unassigned")
    truck = _clean_display_value(row.get("Truck Assigned", ""), "-")
    chassis = _clean_display_value(row.get("Chassis", ""), "-")
    eta = _clean_display_value(row.get("eta", ""), "-")
    exceptions = [item.strip() for item in _safe_str(row.get("Exceptions", "")).split(",") if item.strip()]
    risk = _risk_level(row)

    cols = st.columns([0.6, 1.4, 0.8, 1.1, 1.3, 1.3, 1.0, 1.0, 1.0, 0.9, 1.1, 1.3, 0.7])

    with cols[0]:
        badge = _RISK_BADGE.get(risk, "")
        if badge:
            st.markdown(badge, unsafe_allow_html=True)
    with cols[1]:
        st.markdown(f"**{escape(booking)}**")
        st.caption(f"{container} · {customer}")
    with cols[2]:
        st.caption(move_type)
    with cols[3]:
        st.markdown(render_status_badge(display_status) or escape(display_status), unsafe_allow_html=True)
    with cols[4]:
        st.caption(origin)
    with cols[5]:
        st.caption(destination)
    with cols[6]:
        st.caption(need_date)
        if lfd != "-":
            st.caption(f"LFD {lfd}")
    with cols[7]:
        st.caption(driver)
    with cols[8]:
        st.caption(f"{truck} / {chassis}")
    with cols[9]:
        st.caption(eta)
    with cols[10]:
        if exceptions:
            st.caption(", ".join(exceptions[:2]) + (f" +{len(exceptions) - 2}" if len(exceptions) > 2 else ""))
    with cols[11]:
        _render_row_next_action(row, load_id, move_type, canonical_status, empty_return_required, refresh_callback)
    with cols[12]:
        if st.button("Open", key=f"open_row_{load_id}", use_container_width=True):
            st.session_state["dispatch_board_selected_row_id"] = load_id
            st.rerun()

    with st.expander(f"Details — {booking}", expanded=False):
        detail_cols = st.columns(3)
        detail_cols[0].write(f"**Address:** {_clean_display_value(row.get('Address', ''), '-')}")
        detail_cols[0].write(f"**Terminal:** {_clean_display_value(row.get('terminal', ''), '-')}")
        detail_cols[1].write(f"**Pickup Appt:** {_clean_display_value(row.get('pickup_appointment', ''), '-')}")
        detail_cols[1].write(f"**Delivery Appt:** {_clean_display_value(row.get('delivery_appointment', ''), '-')}")
        detail_cols[2].write(f"**Empty Return:** {_clean_display_value(row.get('empty_return_location', ''), '-')}")
        detail_cols[2].write(f"**Notes:** {_clean_display_value(row.get('Dispatcher Notes', ''), '-')}")
        if exceptions:
            st.warning("Exceptions: " + ", ".join(exceptions))

    st.divider()


def _render_dispatch_row_group(group_row, refresh_callback) -> None:
    """A collapsed multi-container booking: show the summary row (using
    the first container's data) plus a picker to open one specific
    container, reusing the same pattern proven for the column board."""
    row_ids = list(group_row.get("_grouped_row_ids", []))
    load_id = _int_or_none(group_row.get("_row_id")) or 0
    containers_label = group_row.get("Containers") or f"{len(row_ids)} containers"

    _render_dispatch_row(group_row, refresh_callback)
    st.caption(f"📦 {containers_label} in this booking")
    picker_key = f"dispatch_row_group_picker_{load_id}"
    if st.session_state.get(f"{picker_key}_open"):
        for row_id in row_ids:
            if st.button(f"Open container (load {row_id})", key=f"{picker_key}_{row_id}", use_container_width=True):
                st.session_state["dispatch_board_selected_row_id"] = row_id
                st.session_state.pop(f"{picker_key}_open", None)
                st.rerun()
    else:
        if st.button("Show all containers in this booking", key=f"{picker_key}_toggle", use_container_width=True):
            st.session_state[f"{picker_key}_open"] = True
            st.rerun()


def _render_booking_card(card: dict) -> None:
    display_status = get_display_label(card["move_type"], card["canonical_status"])
    visible = card["visible_container_count"]
    total = card["total_container_count"]
    container_label = f"{visible} of {total} containers" if total != visible else (f"{visible} container" if visible == 1 else f"{visible} containers")
    appt = card["earliest_need_date"] or "No appt set"
    lfd_suffix = f" · LFD {card['earliest_lfd']}" if card["earliest_lfd"] else ""
    badges = ""
    if card["exception_count"]:
        badges += f'<span style="background:#fee2e2;color:#991b1b;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;margin-right:4px;">{card["exception_count"]} exception{"s" if card["exception_count"] != 1 else ""}</span>'
    if card["unassigned_count"]:
        badges += f'<span style="background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;">{card["unassigned_count"]} unassigned</span>'
    html = f'<a href="{escape(card["workspace_url"])}" target="_blank" style="text-decoration:none;color:inherit;display:block;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-bottom:10px;background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.05);"><div style="font-weight:700;font-size:13px;color:#0f172a;">{escape(card["booking_number"])}</div><div style="font-size:11px;color:#475569;margin-top:2px;">{escape(card["customer"])} · {escape(card["move_type"])} · {escape(container_label)}</div><div style="font-size:11px;color:#475569;">{escape(display_status)} · {escape(appt)}{escape(lfd_suffix)}</div><div style="margin-top:6px;">{badges}</div></a>'
    st.markdown(html, unsafe_allow_html=True)


def _render_swimlane_board(scope_df: pd.DataFrame, totals_df: pd.DataFrame, completed_df: pd.DataFrame) -> None:
    """One full-width horizontal lane per canonical status. Booking-level
    rollup cards render left-to-right within each lane using
    services.dispatch_card_view_model (never services.load_grouping_service,
    which the row-based board still uses)."""
    cards = build_booking_card_view_models(scope_df, totals_df)
    completed_cards = build_booking_card_view_models(completed_df, completed_df) if not completed_df.empty else []

    by_status: dict[str, list[dict]] = {}
    for card in cards:
        by_status.setdefault(card["canonical_status"], []).append(card)

    lanes = [stage for stage in get_board_columns() if stage != "Completed"]
    for status in lanes:
        lane_cards = sort_booking_cards(by_status.get(status, []))
        booking_count = len(lane_cards)
        container_count = sum(c["visible_container_count"] for c in lane_cards)
        unassigned_count = sum(c["unassigned_count"] for c in lane_cards)
        label = f"{status} — {booking_count} Bookings · {container_count} Containers"
        if unassigned_count:
            label += f" · {unassigned_count} Unassigned"
        with st.expander(label, expanded=bool(lane_cards)):
            if not lane_cards:
                st.caption("No bookings in this stage.")
                continue
            chunk_size = 4
            for start in range(0, len(lane_cards), chunk_size):
                row_cards = lane_cards[start:start + chunk_size]
                cols = st.columns(chunk_size)
                for col, card in zip(cols, row_cards):
                    with col:
                        _render_booking_card(card)

    completed_label = f"Completed — {len(completed_cards)} Bookings · {sum(c['visible_container_count'] for c in completed_cards)} Containers (recent)"
    with st.expander(completed_label, expanded=False):
        if not completed_cards:
            st.caption("No recently completed bookings.")
        else:
            chunk_size = 4
            for start in range(0, len(completed_cards), chunk_size):
                row_cards = completed_cards[start:start + chunk_size]
                cols = st.columns(chunk_size)
                for col, card in zip(cols, row_cards):
                    with col:
                        _render_booking_card(card)


def render_dispatch_board_focused(df: pd.DataFrame, refresh_callback: Callable[[], None] | None = None, port_houston_panel_renderer: Callable | None = None) -> None:
    st.subheader("Dispatch Board")
    st.caption("Action board by move type. Port/PIN work appears only for port imports and exports.")

    if df.empty:
        st.info("No load data is available for Dispatch Board.")
        return

    board_df = df.copy()
    board_df["Status"] = board_df.get("Status", pd.Series("New", index=board_df.index)).fillna("New").astype(str).str.strip()
    board_df["TYPE"] = board_df.get("TYPE", pd.Series("", index=board_df.index)).apply(_normalize_load_type_value)
    selected_flow = render_service_flow_filter("dispatch_board_service_flow")
    board_df = apply_service_flow_filter(board_df, selected_flow)
    board_df["Dispatch Move Type"] = board_df["TYPE"].apply(_normalize_load_type_value)
    board_df["Delivery Date Parsed"] = pd.to_datetime(
        board_df.get("Delivery Need Date", pd.Series("", index=board_df.index)).astype(str).str.strip(),
        errors="coerce",
    )
    board_df["LFD Parsed"] = pd.to_datetime(
        board_df.get("LFD", pd.Series("", index=board_df.index)).astype(str).str.strip(),
        errors="coerce",
    )

    readiness_rows = [_load_readiness_details(row, include_documents=False) for _, row in board_df.iterrows()]
    board_df["Exceptions"] = [", ".join(item.get("exceptions", [])) for item in readiness_rows]
    board_df["Exception Count"] = board_df["Exceptions"].apply(lambda value: len([item for item in _safe_str(value).split(",") if item.strip()]))
    board_df["Is Active Dispatch"] = [
        is_active_dispatch_status(row["Dispatch Move Type"], row["Status"]) for _, row in board_df.iterrows()
    ]

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    controls = st.columns([1.3, 1, 2.4])
    with controls[0]:
        selected_scope = st.radio(
            "Board Scope",
            ["Active Now", "Due Today / Late", "Tomorrow", "Future Pipeline"],
            horizontal=False,
            key="dispatch_board_scope",
        )
    with controls[1]:
        exception_only = st.checkbox("Exceptions only", value=False, key="dispatch_board_exception_only")
    with controls[2]:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, load, container, customer, driver, truck, port, warehouse",
            key="dispatch_board_search",
        )

    def _filter_options(column: str) -> list[str]:
        if column not in board_df.columns:
            return []
        values = board_df[column].astype(str).str.strip()
        return sorted({value for value in values if value and value.lower() not in ("nan", "none")})

    extra_filter_cols = st.columns(4)
    with extra_filter_cols[0]:
        customer_filter = st.selectbox("Customer", ["All"] + _filter_options("Customer"), key="dispatch_board_customer_filter")
    with extra_filter_cols[1]:
        driver_filter = st.selectbox("Driver", ["All"] + _filter_options("Driver Name"), key="dispatch_board_driver_filter")
    with extra_filter_cols[2]:
        port_filter = st.selectbox("Port", ["All"] + _filter_options("Port"), key="dispatch_board_port_filter")
    with extra_filter_cols[3]:
        warehouse_filter = st.selectbox("Warehouse", ["All"] + _filter_options("Warehouse"), key="dispatch_board_warehouse_filter")

    scope_df = board_df[board_df["Is Active Dispatch"]].copy()
    if selected_scope == "Due Today / Late":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().le(today)
        ].copy()
    elif selected_scope == "Tomorrow":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        ].copy()
    elif selected_scope == "Future Pipeline":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        ].copy()

    if exception_only:
        scope_df = scope_df[scope_df["Exception Count"].gt(0)].copy()

    if customer_filter != "All" and "Customer" in scope_df.columns:
        scope_df = scope_df[scope_df["Customer"].astype(str).str.strip().eq(customer_filter)].copy()
    if driver_filter != "All" and "Driver Name" in scope_df.columns:
        scope_df = scope_df[scope_df["Driver Name"].astype(str).str.strip().eq(driver_filter)].copy()
    if port_filter != "All" and "Port" in scope_df.columns:
        scope_df = scope_df[scope_df["Port"].astype(str).str.strip().eq(port_filter)].copy()
    if warehouse_filter != "All" and "Warehouse" in scope_df.columns:
        scope_df = scope_df[scope_df["Warehouse"].astype(str).str.strip().eq(warehouse_filter)].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "Booking Number",
            "Load ID",
            "Reference Number",
            "Container Number",
            "Customer",
            "Port",
            "Warehouse",
            "Address",
            "Driver Name",
            "Truck Assigned",
            "Chassis",
            "Status",
            "Dispatcher Notes",
        ]
        available_columns = [column for column in searchable_columns if column in scope_df.columns]
        search_blob = scope_df[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            mask = search_blob.str.contains(re.escape(term), na=False)
            scope_df = scope_df[mask].copy()
            search_blob = search_blob[mask]

    unassigned_mask = scope_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])

    status_filter = st.selectbox(
        "Status Filter",
        ["All Active"] + get_board_columns(),
        key="dispatch_board_status_filter",
    )
    if status_filter != "All Active":
        scope_df = scope_df[scope_df["Status"].eq(status_filter)].copy()

    # Completed lane uses the same filters (customer/driver/port/warehouse/exceptions/search)
    # as the active scope so metrics, lane counts, and visible cards stay consistent.
    completed_df = board_df[board_df["Status"].eq("Completed")].copy()
    if customer_filter != "All" and "Customer" in completed_df.columns:
        completed_df = completed_df[completed_df["Customer"].astype(str).str.strip().eq(customer_filter)].copy()
    if driver_filter != "All" and "Driver Name" in completed_df.columns:
        completed_df = completed_df[completed_df["Driver Name"].astype(str).str.strip().eq(driver_filter)].copy()
    if port_filter != "All" and "Port" in completed_df.columns:
        completed_df = completed_df[completed_df["Port"].astype(str).str.strip().eq(port_filter)].copy()
    if warehouse_filter != "All" and "Warehouse" in completed_df.columns:
        completed_df = completed_df[completed_df["Warehouse"].astype(str).str.strip().eq(warehouse_filter)].copy()
    if exception_only:
        completed_df = completed_df[completed_df["Exception Count"].gt(0)].copy()
    if search_filter:
        completed_available_columns = [column for column in [
            "Booking Number", "Load ID", "Reference Number", "Container Number",
            "Customer", "Port", "Warehouse", "Address", "Driver Name",
            "Truck Assigned", "Chassis", "Status", "Dispatcher Notes",
        ] if column in completed_df.columns]
        completed_blob = completed_df[completed_available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            completed_df = completed_df[completed_blob.str.contains(re.escape(term), na=False)]
            completed_blob = completed_blob[completed_blob.str.contains(re.escape(term), na=False)]
    completed_df = completed_df.sort_values("_row_id", ascending=False).head(30)

    active_cards = build_booking_card_view_models(scope_df, board_df)
    booking_identities = {(c["booking_number"], c["customer"], c["move_type"]) for c in active_cards}

    summary_cols = st.columns(4)
    summary_cols[0].metric("Bookings", len(booking_identities))
    summary_cols[1].metric("Containers", len(scope_df))
    summary_cols[2].metric("Unassigned", int(unassigned_mask.sum()))
    summary_cols[3].metric("Active Exceptions", int(scope_df["Exception Count"].gt(0).sum()))

    metric_cols = st.columns(6)
    metric_cols[0].metric("Ready to Dispatch", int(scope_df["Status"].eq("Ready to Dispatch").sum()))
    metric_cols[1].metric("En Route", int(scope_df["Status"].isin(["En Route to Pickup", "En Route to Delivery"]).sum()))
    metric_cols[2].metric("At Pickup", int(scope_df["Status"].eq("At Pickup").sum()))
    metric_cols[3].metric("At Delivery", int(scope_df["Status"].eq("At Delivery").sum()))
    metric_cols[4].metric("Empty Returns Due", int(scope_df["Status"].eq("Returning Empty").sum()))
    metric_cols[5].metric("Completed Today", len(completed_df))

    exception_counts = _load_exception_summary(scope_df)
    exception_labels = ["Late appointment", "No PIN", "Waiting driver", "Port hold"]
    exception_cols = st.columns(len(exception_labels))
    for idx, label in enumerate(exception_labels):
        exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))

    if scope_df.empty and completed_df.empty:
        st.info("No active dispatch loads match the current Dispatch Board filters.")
    else:
        _render_swimlane_board(scope_df, board_df, completed_df)

    selected_row_id = st.session_state.get("dispatch_board_selected_row_id")
    if selected_row_id is None:
        st.caption("Open any load card to review dispatch details, sync port data, request PIN, update status, or send the driver packet.")
        return

    selected_df = board_df[board_df["_row_id"].astype(int).eq(int(selected_row_id))].copy() if "_row_id" in board_df.columns else pd.DataFrame()
    if selected_df.empty:
        st.warning("The selected load is no longer available.")
        if st.button("Clear Dispatch Selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
        return

    clear_cols = st.columns([4, 1])
    with clear_cols[0]:
        st.markdown("### Selected Load")
    with clear_cols[1]:
        if st.button("Clear Selection", key="clear_dispatch_board_selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
    render_dispatch_workspace(selected_df.iloc[0], refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer)
