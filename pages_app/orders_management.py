from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, has_permission
from application.exceptions import AuthorizationError
from application.loads.commands import (
    cancel_load,
    mark_load_missing_info,
    mark_load_ready_to_dispatch,
    update_load_fields,
    verify_load_booking,
)
from db_client import DispatchDatabaseClient
from services.tms_data_service import refresh_data as _refresh_tms_data
from services.dispatch_workflow_service import (
    LOAD_TYPE_TABS,
    _generate_driver_dispatch_message,
    _load_has_pin_or_appointment,
    _load_port_verified,
    _load_requires_port,
    _normalize_load_type_value,
    _status_row_style,
)
from services.driver_roster_service import find_driver_in_roster, list_active_drivers
from services.load_grouping_service import group_loads_by_booking
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter


ORDER_MANAGEMENT_STATUSES = [
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Ready to Dispatch",
    "Cancelled",
]

ORDER_MANAGEMENT_STATUS_LABELS = {
    "New": "New",
    "Hold/Need Info": "Missing Info",
    "Booking Verified": "Booking Verified",
    "Ready to Dispatch": "Ready to Dispatch",
    "Cancelled": "Cancel",
}


def _safe_str(value, default: str = "") -> str:
    value_str = str(value if value is not None else default).strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return default
    return value_str


def _parse_date_or_none(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def refresh_data() -> None:
    """Every call site in this file follows a DispatchDatabaseClient()
    write to the loads table - only tms_data_service's load caches are
    affected, so this delegates to its targeted refresh rather than
    wiping every unrelated cache app-wide."""
    _refresh_tms_data()


BOOKING_VERIFICATION_REQUIRED_FIELDS = [
    "TYPE",
    "Booking Number",
    "Customer",
    "Container Number",
    "Port",
    "Warehouse",
    "Delivery Need Date",
    "LFD",
]


def _is_blank_value(value) -> bool:
    value_str = str(value or "").strip()
    return value_str == "" or value_str.lower() in {"nan", "none", "nat", "-", "null"}


def _booking_readiness(row) -> tuple[int, list[str]]:
    missing = []

    for field in BOOKING_VERIFICATION_REQUIRED_FIELDS:
        if field not in row.index or _is_blank_value(row.get(field, "")):
            missing.append(field)

    completed = len(BOOKING_VERIFICATION_REQUIRED_FIELDS) - len(missing)
    score = int(round((completed / len(BOOKING_VERIFICATION_REQUIRED_FIELDS)) * 100))

    return score, missing


def _add_booking_verification_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    scores = []
    missing_values = []
    readiness_labels = []

    for _, row in df.iterrows():
        score, missing = _booking_readiness(row)
        scores.append(score)
        missing_values.append(", ".join(missing) if missing else "")

        if score == 100:
            readiness_labels.append("Ready")
        elif score >= 75:
            readiness_labels.append("Needs Minor Info")
        elif score >= 50:
            readiness_labels.append("Needs Review")
        else:
            readiness_labels.append("Missing Critical Info")

    df["Readiness %"] = scores
    df["Missing Fields"] = missing_values
    df["Verification Result"] = readiness_labels

    return df


def _render_booking_verification_table(table_df: pd.DataFrame, title: str) -> None:
    st.markdown(f"#### {title}")
    st.caption(f"{len(table_df)} booking(s)")

    if table_df.empty:
        st.success("No bookings in this queue.")
        return

    columns = [
        "_row_id",
        "TYPE",
        "Booking Number",
        "Customer",
        "Container Number",
        "Port",
        "Warehouse",
        "Delivery Need Date",
        "LFD",
        "Status",
        "Readiness %",
        "Verification Result",
        "Missing Fields",
        "Dispatcher Notes",
    ]

    display_cols = [c for c in columns if c in table_df.columns]
    styled = (
        table_df.sort_values(["Readiness %", "_row_id"], ascending=[True, False])[display_cols]
        .style
        .apply(_status_row_style, axis=1)
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_booking_verification_actions(verification_df: pd.DataFrame) -> None:
    if verification_df.empty:
        return

    st.divider()
    st.markdown("### Booking Final Check")
    st.caption("Use this section as the last office check before the order becomes Booking Verified. Port sync, PIN, driver assignment, and dispatch packet happen in the load workspace.")

    labels = [
        f"{row['Booking Number']} | {row.get('Customer', '')} | {row.get('Readiness %', 0)}% ready | row {int(row['_row_id'])}"
        for _, row in verification_df.sort_values("_row_id", ascending=False).iterrows()
    ]

    selected = st.selectbox("Select booking to review", labels, key="booking_verification_selected")
    selected_row_id = int(selected.split("row ")[-1])
    selected_df = verification_df[verification_df["_row_id"].astype(int).eq(selected_row_id)]

    if selected_df.empty:
        st.warning("Selected booking was not found.")
        return

    selected_load = selected_df.iloc[0]
    readiness_score = int(selected_load.get("Readiness %", 0))
    missing_fields = str(selected_load.get("Missing Fields", "") or "")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Booking", str(selected_load.get("Booking Number", "") or "-"))
    c2.metric("Customer", str(selected_load.get("Customer", "") or "-"))
    c3.metric("Readiness", f"{readiness_score}%")
    c4.metric("Status", str(selected_load.get("Status", "") or "-"))

    if missing_fields:
        st.warning(f"Missing fields: {missing_fields}")
    else:
        st.success("This booking has all required dispatch-readiness fields.")

    with st.expander("Review selected booking details", expanded=True):
        details = {
            "Type": selected_load.get("TYPE", ""),
            "Booking Number": selected_load.get("Booking Number", ""),
            "Load ID": selected_load.get("Load ID", ""),
            "Customer": selected_load.get("Customer", ""),
            "Container Number": selected_load.get("Container Number", ""),
            "Port / Pickup": selected_load.get("Port", ""),
            "Warehouse / Delivery": selected_load.get("Warehouse", ""),
            "Delivery Need Date": selected_load.get("Delivery Need Date", ""),
            "LFD": selected_load.get("LFD", ""),
            "Status": selected_load.get("Status", ""),
            "Dispatcher Notes": selected_load.get("Dispatcher Notes", ""),
        }
        st.dataframe(
            pd.DataFrame([{"Field": k, "Value": v} for k, v in details.items()]),
            use_container_width=True,
            hide_index=True,
        )

    action_note = st.text_area(
        "Verification Note",
        value=str(selected_load.get("Dispatcher Notes", "") or ""),
        height=100,
        key=f"booking_verification_note_{selected_row_id}",
    )

    a1, a2, a3, a4 = st.columns(4)

    with a1:
        if st.button("Mark Missing Info", key=f"mark_missing_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Hold/Need Info",
                    "Dispatcher Notes": action_note or f"Missing fields: {missing_fields}",
                },
            )
            refresh_data()
            st.success("Booking marked Hold/Need Info.")
            st.rerun()

    with a2:
        if st.button("Save Verification Note", key=f"save_verify_note_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Dispatcher Notes": action_note},
            )
            refresh_data()
            st.success("Verification note saved.")
            st.rerun()

    with a3:
        disabled = readiness_score < 100
        if st.button(
            "Mark Booking Verified",
            key=f"mark_booking_verified_{selected_row_id}",
            use_container_width=True,
            disabled=disabled,
            help="Requires 100% booking completeness. The next action will be Port Houston verification.",
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Booking Verified",
                    "Dispatcher Notes": action_note or "Booking information verified. Next action: verify booking with Port Houston.",
                },
            )
            refresh_data()
            st.success("Booking marked verified. Open the load workspace for Port Sync / PIN.")
            st.rerun()

    with a4:
        if st.button("Cancel Booking", key=f"verification_cancel_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Status": "Cancelled", "Dispatcher Notes": action_note or "Booking cancelled during review."},
            )
            refresh_data()
            st.error("Booking cancelled.")
            st.rerun()

    if readiness_score < 100:
        st.info("Mark Booking Verified is disabled until all required booking fields are complete.")

def render_booking_review(df: pd.DataFrame) -> None:
    st.markdown("### Booking Review")
    st.caption("Complete missing booking information here. Verified bookings move to the load workspace for Port Sync / PIN.")

    review_statuses = ["New", "Hold/Need Info", "Booking Verified"]
    review_df = df[df["Status"].isin(review_statuses)].copy()
    review_df = _add_booking_verification_columns(review_df)

    if review_df.empty:
        st.success("No bookings require review.")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Needs Review", int(review_df["Status"].eq("New").sum()))
    k2.metric("Missing Info", int(review_df["Status"].eq("Hold/Need Info").sum()))
    k3.metric("Booking Verified", int(review_df["Status"].eq("Booking Verified").sum()))
    k4.metric("Complete", int(review_df["Readiness %"].eq(100).sum()))

    q1, q2, q3, q4 = st.tabs(
        ["New Orders", "Missing Information", "Booking Verified", "All Review"]
    )

    with q1:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("New")].copy(),
            "New Orders",
        )

    with q2:
        missing_df = review_df[review_df["Readiness %"].lt(100)].copy()
        _render_booking_verification_table(missing_df, "Missing Information")

    with q3:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("Booking Verified")].copy(),
            "Booking Verified",
        )

    with q4:
        _render_booking_verification_table(review_df, "All Bookings in Review")

    st.divider()
    st.markdown("### Edit Selected Booking")

    labels = [
        f"{row['Booking Number']} | {row.get('Customer', '')} | {row.get('Readiness %', 0)}% ready | row {int(row['_row_id'])}"
        for _, row in review_df.sort_values("_row_id", ascending=False).iterrows()
    ]

    selected = st.selectbox("Select booking to edit", labels, key="booking_review_selected")
    selected_row_id = int(selected.split("row ")[-1])
    selected_df = review_df[review_df["_row_id"].astype(int).eq(selected_row_id)]

    if selected_df.empty:
        st.warning("Selected booking was not found.")
        return

    selected_load = selected_df.iloc[0]
    readiness_score = int(selected_load.get("Readiness %", 0))
    missing_fields = str(selected_load.get("Missing Fields", "") or "")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Booking", _safe_str(selected_load.get("Booking Number", "")) or "-")
    m2.metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    m3.metric("Readiness", f"{readiness_score}%")
    m4.metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    if missing_fields:
        st.warning(f"Missing fields: {missing_fields}")
    else:
        st.success("This booking is complete and ready to dispatch.")

    with st.form(f"booking_review_form_{selected_row_id}"):
        c1, c2, c3 = st.columns(3)

        with c1:
            type_val = st.selectbox(
                "TYPE",
                LOAD_TYPE_TABS,
                index=LOAD_TYPE_TABS.index(_normalize_load_type_value(_safe_str(selected_load.get("TYPE", ""))))
                if _normalize_load_type_value(_safe_str(selected_load.get("TYPE", ""))) in LOAD_TYPE_TABS
                else 0,
            )
            booking = st.text_input("Booking Number *", value=_safe_str(selected_load.get("Booking Number", "")))
            load_id = st.text_input("Load ID", value=_safe_str(selected_load.get("Load ID", "")))
            reference = st.text_input("Reference Number", value=_safe_str(selected_load.get("Reference Number", "")))
            customer = st.text_input("Customer *", value=_safe_str(selected_load.get("Customer", "")))
            container = st.text_input("Container Number *", value=_safe_str(selected_load.get("Container Number", "")))

        with c2:
            port = st.text_input("Port / Pickup *", value=_safe_str(selected_load.get("Port", "")))
            warehouse = st.text_input("Warehouse / Delivery *", value=_safe_str(selected_load.get("Warehouse", "")))
            address = st.text_input("Address", value=_safe_str(selected_load.get("Address", "")))
            delivery_need = st.date_input(
                "Delivery Need Date *",
                value=_parse_date_or_none(selected_load.get("Delivery Need Date", "")),
            )
            lfd = st.date_input(
                "LFD",
                value=_parse_date_or_none(selected_load.get("LFD", "")),
            )
            size = st.selectbox(
                "Size",
                ["", "20", "40", "40HC", "40ST", "20FR", "40FR", "20 STRF", "40STRF"],
                index=0,
            )

        with c3:
            review_status_options = list(ORDER_MANAGEMENT_STATUSES)
            current_review_status = _safe_str(selected_load.get("Status", "New"))
            if current_review_status and current_review_status not in review_status_options:
                review_status_options.insert(0, current_review_status)
            status = st.selectbox(
                "Review Status",
                review_status_options,
                index=review_status_options.index(current_review_status)
                if current_review_status in review_status_options
                else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
            )
            driver = st.text_input("Driver Name", value=_safe_str(selected_load.get("Driver Name", "")))
            truck = st.text_input("Truck Assigned", value=_safe_str(selected_load.get("Truck Assigned", "")))
            chassis = st.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")))
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=165,
            )

        submitted = st.form_submit_button("Save Booking Review")

    if submitted:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "driver_name": driver.strip(),
            "truck_assigned": truck.strip(),
            "chassis": chassis.strip(),
            "dispatcher_notes": notes.strip(),
    }
        

        DispatchDatabaseClient().update_row_fields(selected_row_id, updates)
        refresh_data()
        st.success("Booking review saved.")
        st.rerun()

    st.markdown("### Booking Actions")

    a1, a2, a3, a4 = st.columns(4)

    with a1:
        if st.button("Mark Missing Info", key=f"review_missing_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Hold/Need Info",
                    "Dispatcher Notes": missing_fields or "Missing booking information.",
                },
            )
            refresh_data()
            st.warning("Booking marked Hold/Need Info.")
            st.rerun()

    with a2:
        ready_disabled = readiness_score < 100
        if st.button(
            "Mark Booking Verified",
            key=f"review_booking_verified_{selected_row_id}",
            use_container_width=True,
            disabled=ready_disabled,
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Booking Verified",
                    "Dispatcher Notes": "Booking completed and verified. Next action: verify booking with Port Houston.",
                },
            )
            refresh_data()
            st.success("Booking marked verified. Open the load workspace for Port Sync / PIN.")
            st.rerun()

    with a3:
        if st.button("Save Notes", key=f"review_save_notes_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Dispatcher Notes": notes.strip()},
            )
            refresh_data()
            st.success("Booking notes saved.")
            st.rerun()

    with a4:
        if st.button("Cancel Booking", key=f"review_cancel_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Status": "Cancelled"},
            )
            refresh_data()
            st.error("Booking cancelled.")
            st.rerun()

    if readiness_score < 100:
        st.info("Mark Booking Verified is disabled until all required fields are complete.")

def _render_order_detail_editor(
    work_df: pd.DataFrame, selected_row_id: int, context_key: str, principal: AuthenticatedActor
) -> None:
    selected_df = work_df[work_df["_row_id"].astype(int).eq(int(selected_row_id))]

    if selected_df.empty:
        st.warning("Selected order was not found.")
        return

    selected_load = selected_df.iloc[0]
    safe_context = re.sub(r"[^A-Za-z0-9_]+", "_", context_key)
    form_key = f"order_detail_editor_{safe_context}_{selected_row_id}"

    header_cols = st.columns([4, 1])
    with header_cols[0]:
        st.markdown("### Order Detail Editor")
        st.caption(
            f"Editing: {selected_load.get('Booking Number', '')} | "
            f"{selected_load.get('Customer', '')} | row {selected_row_id}"
        )
    with header_cols[1]:
        if st.button("Clear Editor", key=f"clear_order_editor_{safe_context}_{selected_row_id}", use_container_width=True):
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            st.rerun()

    with st.form(form_key):
        c1, c2, c3 = st.columns(3)

        with c1:
            type_val = st.selectbox(
                "TYPE",
                LOAD_TYPE_TABS,
                index=LOAD_TYPE_TABS.index(_normalize_load_type_value(_safe_str(selected_load.get("TYPE", ""))))
                if _normalize_load_type_value(_safe_str(selected_load.get("TYPE", ""))) in LOAD_TYPE_TABS else 0,
                key=f"{form_key}_type",
            )
            booking = st.text_input("Booking Number", value=_safe_str(selected_load.get("Booking Number", "")), key=f"{form_key}_booking")
            load_id = st.text_input("Load ID", value=_safe_str(selected_load.get("Load ID", "")), key=f"{form_key}_load_id")
            reference = st.text_input("Reference Number", value=_safe_str(selected_load.get("Reference Number", "")), key=f"{form_key}_reference")
            customer = st.text_input("Customer", value=_safe_str(selected_load.get("Customer", "")), key=f"{form_key}_customer")
            container = st.text_input("Container Number", value=_safe_str(selected_load.get("Container Number", "")), key=f"{form_key}_container")

        with c2:
            port = st.text_input("Port / Pickup", value=_safe_str(selected_load.get("Port", "")), key=f"{form_key}_port")
            warehouse = st.text_input("Warehouse / Delivery", value=_safe_str(selected_load.get("Warehouse", "")), key=f"{form_key}_warehouse")
            address = st.text_input("Address", value=_safe_str(selected_load.get("Address", "")), key=f"{form_key}_address")
            delivery_need = st.date_input(
                "Delivery Need Date",
                value=_parse_date_or_none(selected_load.get("Delivery Need Date", "")),
                key=f"{form_key}_delivery_need",
            )
            lfd = st.date_input(
                "LFD",
                value=_parse_date_or_none(selected_load.get("LFD", "")),
                key=f"{form_key}_lfd",
            )

        with c3:
            current_order_status = _safe_str(selected_load.get("Status", "New"))
            order_status_options = list(ORDER_MANAGEMENT_STATUSES)
            if current_order_status and current_order_status not in order_status_options:
                order_status_options.insert(0, current_order_status)
            status = st.selectbox(
                "Status",
                order_status_options,
                index=order_status_options.index(current_order_status)
                if current_order_status in order_status_options else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
                key=f"{form_key}_status",
            )
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=135,
                key=f"{form_key}_notes",
            )

        can_edit = has_permission(principal, Permission.LOAD_EDIT)
        if not can_edit:
            st.caption("Your role does not have permission to edit orders.")
        save_order = st.form_submit_button("Save Order Updates", disabled=not can_edit)

    if save_order:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "dispatcher_notes": notes.strip(),
        }

        try:
            update_load_fields(actor=principal, load_id=selected_row_id, updates=updates)
        except AuthorizationError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            refresh_data()
            st.success("Order updated successfully.")
            st.rerun()

    st.markdown("#### Quick Actions")
    q1, q2, q3 = st.columns(3)
    with q1:
        can_edit = has_permission(principal, Permission.LOAD_EDIT)
        if st.button(
            "Mark Missing Info",
            key=f"quick_missing_info_{safe_context}_{selected_row_id}",
            use_container_width=True,
            disabled=not can_edit,
        ):
            try:
                mark_load_missing_info(
                    actor=principal,
                    load_id=selected_row_id,
                    note=notes.strip() or "Missing information requested from customer.",
                )
            except AuthorizationError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("orders_management_selected_row_id", None)
                st.session_state.pop("orders_management_selected_context", None)
                refresh_data()
                st.warning("Order marked Hold/Need Info.")
                st.rerun()
    with q2:
        can_verify = has_permission(principal, Permission.LOAD_VERIFY)
        if st.button(
            "Mark Booking Verified",
            key=f"quick_booking_verified_{safe_context}_{selected_row_id}",
            use_container_width=True,
            disabled=not can_verify,
        ):
            try:
                verify_load_booking(
                    actor=principal,
                    load_id=selected_row_id,
                    note=notes.strip()
                    or "Order reviewed and booking verified. Next action: verify booking with Port Houston.",
                )
            except AuthorizationError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("orders_management_selected_row_id", None)
                st.session_state.pop("orders_management_selected_context", None)
                refresh_data()
                st.success("Order marked Booking Verified.")
                st.rerun()
    with q3:
        can_cancel = has_permission(principal, Permission.LOAD_CANCEL)
        if st.button(
            "Cancel Order",
            key=f"quick_cancel_order_{safe_context}_{selected_row_id}",
            use_container_width=True,
            disabled=not can_cancel,
        ):
            try:
                cancel_load(actor=principal, load_id=selected_row_id)
            except AuthorizationError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("orders_management_selected_row_id", None)
                st.session_state.pop("orders_management_selected_context", None)
                refresh_data()
                st.error("Order cancelled.")
                st.rerun()

    if not (can_edit and can_verify and can_cancel):
        st.caption("Some quick actions are disabled for your role.")


def _render_ready_to_dispatch_panel(
    work_df: pd.DataFrame, selected_row_id: int, context_key: str, principal: AuthenticatedActor
) -> None:
    selected_df = work_df[work_df["_row_id"].astype(int).eq(int(selected_row_id))]

    if selected_df.empty:
        st.warning("Selected order was not found.")
        return

    selected_load = selected_df.iloc[0]
    safe_context = re.sub(r"[^A-Za-z0-9_]+", "_", context_key)
    panel_key = f"ready_to_dispatch_{safe_context}_{selected_row_id}"

    st.markdown("### Ready to Dispatch")
    st.caption(
        f"Assign driver, truck, and chassis, then mark Ready to Dispatch. "
        f"Editing: {selected_load.get('Booking Number', '')} | "
        f"{selected_load.get('Customer', '')} | row {selected_row_id}"
    )

    summary_cols = st.columns(4)
    summary_cols[0].metric("Booking", _safe_str(selected_load.get("Booking Number", "")) or "-")
    summary_cols[1].metric("Container", _safe_str(selected_load.get("Container Number", "")) or "-")
    summary_cols[2].metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    summary_cols[3].metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    with st.expander("Order details", expanded=False):
        details = {
            "Port / Pickup": selected_load.get("Port", ""),
            "Warehouse / Delivery": selected_load.get("Warehouse", ""),
            "Delivery Need Date": selected_load.get("Delivery Need Date", ""),
            "LFD": selected_load.get("LFD", ""),
        }
        st.dataframe(
            pd.DataFrame([{"Field": k, "Value": v} for k, v in details.items()]),
            use_container_width=True,
            hide_index=True,
        )

    if _load_requires_port(selected_load) and not (
        _load_port_verified(selected_load) or _load_has_pin_or_appointment(selected_load)
    ):
        st.warning(
            "Port Verified / PIN is not complete yet. This load can still be marked "
            "Ready to Dispatch, but the Dispatch Board will flag it as an exception "
            "until Port Sync / PIN is done."
        )

    roster_df = list_active_drivers()
    current_driver_name = _safe_str(selected_load.get("Driver Name", ""))
    current_in_roster = find_driver_in_roster(roster_df, current_driver_name) if current_driver_name else None

    roster_options = ["Other / not in roster"]
    if current_driver_name and not current_in_roster:
        roster_options.append(f"{current_driver_name} (not in roster)")
    roster_options.extend(
        f"{row['driver_name']} ({_safe_str(row.get('truck_number', '')) or 'no truck on file'})"
        for _, row in roster_df.iterrows()
    )

    driver_choice = st.selectbox("Driver", roster_options, key=f"{panel_key}_driver_choice")

    if driver_choice == "Other / not in roster":
        driver_name = st.text_input(
            "Driver Name",
            value=current_driver_name if not current_in_roster else "",
            key=f"{panel_key}_driver_manual",
        )
        default_truck = _safe_str(selected_load.get("Truck Assigned", ""))
        default_phone = ""
    elif driver_choice.endswith("(not in roster)"):
        driver_name = current_driver_name
        default_truck = _safe_str(selected_load.get("Truck Assigned", ""))
        default_phone = ""
    else:
        typed_name = driver_choice.rsplit(" (", 1)[0]
        roster_match = find_driver_in_roster(roster_df, typed_name)
        driver_name = roster_match["driver_name"] if roster_match else typed_name
        default_truck = (roster_match or {}).get("truck_number") or ""
        default_phone = (roster_match or {}).get("phone") or ""

    field_cols = st.columns(3)
    truck = field_cols[0].text_input(
        "Truck Assigned",
        value=default_truck,
        key=f"{panel_key}_truck_{driver_choice}",
    )
    chassis = field_cols[1].text_input(
        "Chassis",
        value=_safe_str(selected_load.get("Chassis", "")),
        key=f"{panel_key}_chassis",
    )
    phone = field_cols[2].text_input(
        "Driver Phone",
        value=default_phone,
        key=f"{panel_key}_phone_{driver_choice}",
    )

    st.markdown("#### Generated Dispatch Message")
    preview_load = selected_load.copy()
    preview_load["Driver Name"] = driver_name
    preview_load["Truck Assigned"] = truck
    preview_load["Chassis"] = chassis
    generated_message = _generate_driver_dispatch_message(preview_load)
    edited_message = st.text_area(
        "Dispatch Message",
        value=generated_message,
        height=260,
        key=f"{panel_key}_message",
    )
    if phone.strip():
        st.caption(f"Driver phone on file: {phone.strip()}")

    can_dispatch = has_permission(principal, Permission.LOAD_READY_TO_DISPATCH) and has_permission(
        principal, Permission.DRIVER_MESSAGE_SEND
    )
    ready_disabled = not (driver_name.strip() and truck.strip() and chassis.strip() and phone.strip()) or not can_dispatch
    if st.button(
        "Mark Ready to Dispatch",
        key=f"{panel_key}_mark_ready",
        use_container_width=True,
        disabled=ready_disabled,
    ):
        try:
            result = mark_load_ready_to_dispatch(
                actor=principal,
                load_id=selected_row_id,
                driver_name=driver_name,
                truck=truck,
                chassis=chassis,
                phone=phone,
                message=edited_message,
                note=_safe_str(selected_load.get("Dispatcher Notes", "")),
            )
        except AuthorizationError as exc:
            st.error(str(exc))
        else:
            if result.ok:
                st.session_state.pop("orders_management_selected_row_id", None)
                st.session_state.pop("orders_management_selected_context", None)
                refresh_data()
                st.success(f"Load marked Ready to Dispatch. Text to {driver_name} is queued for delivery.")
                st.rerun()
            else:
                st.error(f"Could not queue the text — no changes were made. {result.reason}")

    if not can_dispatch:
        st.caption("Your role does not have permission to mark loads Ready to Dispatch.")
    elif ready_disabled:
        st.info("Mark Ready to Dispatch is disabled until Driver, Truck, Chassis, and Phone are all filled in.")


def render_orders_management(df: pd.DataFrame, principal: AuthenticatedActor) -> None:
    st.subheader("Orders / Load Management")
    st.caption("Review newly created orders, resolve missing information, mark bookings verified, or cancel bad orders before dispatch work begins.")

    work_df = df.copy()
    work_df["TYPE"] = work_df.get("TYPE", pd.Series("", index=work_df.index)).apply(_normalize_load_type_value)
    selected_flow = render_service_flow_filter("orders_management_service_flow")
    work_df = apply_service_flow_filter(work_df, selected_flow)

    new_df = work_df[work_df["Status"].eq("New")].copy()
    missing_info_df = work_df[work_df["Status"].eq("Hold/Need Info")].copy()
    verified_df = work_df[work_df["Status"].eq("Booking Verified")].copy()
    cancelled_df = work_df[work_df["Status"].eq("Cancelled")].copy()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("New", len(new_df))
    k2.metric("Missing Info", len(missing_info_df))
    k3.metric("Booking Verified", len(verified_df))
    k4.metric("Cancel", len(cancelled_df))

    columns = [
        "_row_id", "TYPE", "Booking Number", "Containers", "Load ID", "Customer",
        "Container Number", "Port", "Warehouse", "Delivery Need Date",
        "LFD", "Status", "Driver Name", "Truck Assigned",
        "Chassis", "Dispatcher Notes",
    ]

    def clear_order_editor() -> None:
        st.session_state.pop("orders_management_selected_row_id", None)
        st.session_state.pop("orders_management_selected_context", None)

    if st.session_state.get("orders_management_last_service_flow") != selected_flow:
        st.session_state["orders_management_last_service_flow"] = selected_flow
        clear_order_editor()

    def render_clickable_order_table(table_df: pd.DataFrame, title: str, detail_renderer=_render_order_detail_editor):
        st.markdown(f"### {title}")
        st.caption(f"{len(table_df)} order(s)")

        if table_df.empty:
            st.info(f"No {title.lower()} orders.")
            return

        grouped_df = group_loads_by_booking(table_df)
        display_cols = [c for c in columns if c in grouped_df.columns]
        sorted_type_df = grouped_df.sort_values("_row_id", ascending=False)
        context_key = f"{title}_{selected_flow}"
        styled_type_df = (
            sorted_type_df[display_cols]
            .style.apply(_status_row_style, axis=1)
            .map(
                lambda value: "font-weight: 800; color: #003B8E;" if value else "",
                subset=["Containers"],
            )
        )

        event = st.dataframe(
            styled_type_df,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key=f"orders_table_{title}_{selected_flow}",
        )

        selected_rows = event.selection.rows

        if selected_rows:
            selected_group_ids = list(sorted_type_df.iloc[selected_rows[0]]["_grouped_row_ids"])
            st.session_state["orders_management_selected_group_ids"] = selected_group_ids
            st.session_state["orders_management_selected_context"] = context_key
            if len(selected_group_ids) == 1:
                st.session_state["orders_management_selected_row_id"] = int(selected_group_ids[0])
            else:
                st.session_state.pop("orders_management_selected_row_id", None)

        selected_context = st.session_state.get("orders_management_selected_context")
        selected_group_ids = st.session_state.get("orders_management_selected_group_ids")
        selected_row_id = st.session_state.get("orders_management_selected_row_id")

        if selected_context != context_key:
            return

        if selected_group_ids and len(selected_group_ids) > 1:
            st.divider()
            st.markdown(f"#### {len(selected_group_ids)} containers in this booking")
            containers_df = work_df[work_df["_row_id"].astype(int).isin(selected_group_ids)]
            container_cols = [c for c in ["_row_id", "Container Number", "Status", "Driver Name", "Delivery Need Date"] if c in containers_df.columns]
            container_event = st.dataframe(
                containers_df[container_cols],
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
                key=f"orders_table_containers_{context_key}",
            )
            if container_event.selection.rows:
                picked_row_id = int(containers_df.iloc[container_event.selection.rows[0]]["_row_id"])
                st.session_state["orders_management_selected_row_id"] = picked_row_id
                selected_row_id = picked_row_id

        if selected_row_id is not None:
            visible_ids = set(work_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                detail_renderer(work_df, int(selected_row_id), context_key, principal)

    queue_options = [
        "New",
        "Missing Info",
        "Booking Verified",
        "Ready to Dispatch",
        "Cancel",
    ]
    queue_map = {
        "New": new_df,
        "Missing Info": missing_info_df,
        "Booking Verified": verified_df,
        "Ready to Dispatch": verified_df,
        "Cancel": cancelled_df,
    }
    queue_detail_renderers = {
        "Ready to Dispatch": _render_ready_to_dispatch_panel,
    }

    selected_queue = st.radio("Order Queue", queue_options, horizontal=True, key="orders_management_queue")
    if st.session_state.get("orders_management_last_queue") != selected_queue:
        st.session_state["orders_management_last_queue"] = selected_queue
        clear_order_editor()

    render_clickable_order_table(
        queue_map[selected_queue],
        selected_queue,
        detail_renderer=queue_detail_renderers.get(selected_queue, _render_order_detail_editor),
    )

    st.caption("Select any order row to edit it under that queue. Changing queue or service flow clears the previous editor.")
