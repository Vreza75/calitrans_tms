from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

import services.operations_inbox_service as ops
from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, has_permission, require_permission
from application.exceptions import AuthorizationError
from application.port_houston.commands import apply_port_houston_data, apply_port_houston_extra_columns
from db_client import DispatchDatabaseClient, execute, read_df, require_schema_ready
from services.tms_data_service import refresh_data as _refresh_tms_data
from utils.text_helpers import json_dump as _json_dump
from services.customer_status_email_service import _get_app_setting
from services.dispatch_workflow_service import (
    _first_present,
    _load_has_driver,
    _load_has_truck,
    _load_pin_display,
    _status_at_or_after,
)
from services.port_houston_client import (
    BOOKING_FIELDS,
    PortHoustonClient,
    PortHoustonError,
    UNIT_FIELDS,
    VESSEL_FIELDS,
    content_records,
    flatten_record,
    get_nested,
    get_port_houston_settings,
    summarize_unit,
)


def _safe_str(value, default: str = "") -> str:
    value_str = str(value if value is not None else default).strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return default
    return value_str


def _existing_load_columns() -> set[str]:
    if hasattr(ops, "existing_load_columns"):
        return ops.existing_load_columns()

    try:
        columns_df = read_df(
            """
            select column_name
            from information_schema.columns
            where table_name = 'loads'
            """
        )
        return set(columns_df["column_name"].astype(str).tolist())
    except Exception:
        return set()


def refresh_data() -> None:
    """Every call site here follows a write to the loads table (PIN/
    appointment/container sync) - only tms_data_service's load caches are
    affected, so this delegates to its targeted refresh rather than
    wiping every unrelated cache app-wide (Operations Inbox, cases,
    attachments)."""
    _refresh_tms_data()


PORT_HOUSTON_ENDPOINTS = {
    "Container / Unit": {
        "endpoint": "/inventory/units",
        "fields": UNIT_FIELDS,
        "hint": "Container availability, yard position, facility, line, routing, and visit state.",
    },
    "Booking": {
        "endpoint": "/orders/bookings",
        "fields": BOOKING_FIELDS,
        "hint": "Booking changes, line, vessel visit, equipment, quantity, and tally status.",
    },
    "Vessel Visit": {
        "endpoint": "/vessel/vesselvisits",
        "fields": VESSEL_FIELDS,
        "hint": "Vessel ETA/ETD, begin receive, cargo cutoff, empty pickup, and first availability.",
    },
    "Gate Appointments": {"endpoint": "/road/gateappointments", "fields": "", "hint": "Existing appointment visibility."},
    "Appointment Time Slots": {"endpoint": "/road/appointmenttimeslots", "fields": "", "hint": "Available appointment windows."},
    "Gate Transactions": {"endpoint": "/road/gatetransactions", "fields": "", "hint": "Ingate/outgate, trouble status, and gate stages."},
    "Truck Visits": {"endpoint": "/road/truckvisits", "fields": "", "hint": "Truck visit status."},
    "Service Events": {"endpoint": "/service/events", "fields": "", "hint": "Operational event history."},
}

PORT_HOUSTON_SUBSCRIPTION_EVENTS = [
    "Unit",
    "Booking",
    "GateAppointment",
    "TruckTransaction",
    "TruckVisit",
    "TruckVisitAppointment",
    "MoveEvent",
    "ServiceOrder",
    "VesselVisit",
    "VesselBerthing",
    "AppointmentTimeSlot",
    "AppointmentQuotaRule",
]

PORT_HOUSTON_APPOINTMENT_TRAN_TYPES = {
    "Deliver Import": "DI",
    "Deliver Empty": "DM",
    "Deliver Chassis": "DC",
    "Deliver Export": "DE",
    "Receive Export": "RE",
    "Receive Empty": "RM",
}


def _ensure_port_houston_sync_log_table() -> None:
    """Verify port_houston_sync_log is present - see
    database/port_houston_integration_migration.sql, which is the sole
    owner of this schema. Raises SchemaNotReadyError if that migration
    has not been applied; never runs DDL itself."""
    require_schema_ready(
        "port_houston_sync_log", "action_type", migration_hint="database/port_houston_integration_migration.sql"
    )


def _log_port_houston_event(
    *,
    action_type: str,
    lookup_type: str = "",
    request_reference: str = "",
    response_summary: dict | None = None,
    load_id=None,
    status: str = "success",
    error_message: str = "",
) -> None:
    try:
        _ensure_port_houston_sync_log_table()
        execute(
            """
            insert into port_houston_sync_log (
                load_id,
                action_type,
                lookup_type,
                request_reference,
                response_summary,
                status,
                error_message
            )
            values (
                :load_id,
                :action_type,
                :lookup_type,
                :request_reference,
                cast(:response_summary as jsonb),
                :status,
                :error_message
            )
            """,
            {
                "load_id": int(load_id) if load_id not in [None, ""] else None,
                "action_type": action_type,
                "lookup_type": lookup_type or None,
                "request_reference": request_reference or None,
                "response_summary": _json_dump(response_summary or {}),
                "status": status,
                "error_message": error_message or None,
            },
        )
    except Exception:
        pass


def _redacted_config_value(value: str) -> str:
    value = _safe_str(value)
    if not value:
        return "Not set"
    if len(value) <= 10:
        return "Set"
    return f"{value[:4]}...{value[-4:]}"


def _get_port_houston_client_or_none() -> PortHoustonClient | None:
    try:
        return PortHoustonClient()
    except PortHoustonError as exc:
        st.warning(str(exc))
        return None


def _port_houston_records_df(records: list[dict], mode: str = "flat") -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    rows = [summarize_unit(record) for record in records] if mode == "unit" else [flatten_record(record) for record in records]
    return pd.DataFrame(rows)


def _store_port_houston_result(key: str, data, lookup_type: str, reference: str, load_id=None) -> None:
    records = content_records(data)
    st.session_state[key] = {
        "data": data,
        "records": records,
        "lookup_type": lookup_type,
        "reference": reference,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _log_port_houston_event(
        action_type="lookup",
        lookup_type=lookup_type,
        request_reference=reference,
        response_summary={"record_count": len(records)},
        load_id=load_id,
    )


def _render_port_houston_result(key: str, mode: str = "flat") -> list[dict]:
    result = st.session_state.get(key)
    if not result:
        return []

    records = result.get("records") or []
    st.caption(f"Last checked: {result.get('checked_at', '')} | {len(records)} record(s)")
    result_df = _port_houston_records_df(records, mode=mode)
    if not result_df.empty:
        st.dataframe(result_df, use_container_width=True, hide_index=True)
    with st.expander("Raw API Response", expanded=False):
        st.json(result.get("data", {}))
    return records


def _port_houston_load_label(row) -> str:
    booking = _safe_str(row.get("Booking Number", "")) or "No booking"
    container = _safe_str(row.get("Container Number", "")) or "No container"
    customer = _safe_str(row.get("Customer", "")) or "No customer"
    row_id = _safe_str(row.get("_row_id", ""))
    return f"{booking} | {container} | {customer} | row {row_id}"


def _port_houston_load_options(df: pd.DataFrame) -> list[dict]:
    if df.empty or "_row_id" not in df.columns:
        return []
    active_df = df[~df["Status"].isin(["Closed", "Cancelled", "Invoiced"])].copy() if "Status" in df.columns else df.copy()
    return [row.to_dict() for _, row in active_df.sort_values("_row_id", ascending=False).head(250).iterrows()]


def _append_port_houston_notes(existing: str, summary: dict) -> str:
    lines = ["Port Houston EVP update:"]
    for key, value in summary.items():
        if _safe_str(value):
            lines.append(f"{key}: {value}")
    note = "\n".join(lines)
    existing = _safe_str(existing)
    return note if not existing else f"{existing}\n\n{note}"


def _updates_from_port_houston_unit(load_row: dict, unit_record: dict) -> dict:
    summary = summarize_unit(unit_record)
    updates = {}
    if _safe_str(summary.get("Container", "")) and not _safe_str(load_row.get("Container Number", "")):
        updates["Container Number"] = summary["Container"]
    if _safe_str(summary.get("Size", "")) and not _safe_str(load_row.get("Size", "")):
        updates["Size"] = summary["Size"]
    if _safe_str(summary.get("Facility", "")) and not _safe_str(load_row.get("Port", "")):
        updates["Port"] = summary["Facility"]
    updates["Dispatcher Notes"] = _append_port_houston_notes(load_row.get("Dispatcher Notes", ""), summary)
    return updates


def _updates_from_port_houston_booking(load_row: dict, booking_record: dict) -> dict:
    updates = {}
    booking = _safe_str(booking_record.get("nbr", ""))
    if booking and not _safe_str(load_row.get("Booking Number", "")):
        updates["Booking Number"] = booking
    client_ref = _safe_str(booking_record.get("clientRefNo", ""))
    if client_ref and not _safe_str(load_row.get("Reference Number", "")):
        updates["Reference Number"] = client_ref
    if _safe_str(booking_record.get("destination", "")) and not _safe_str(load_row.get("Warehouse", "")):
        updates["Warehouse"] = _safe_str(booking_record.get("destination", ""))

    first_item = {}
    items = booking_record.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        first_item = items[0]
    size = " ".join(
        [
            part
            for part in [
                _safe_str(first_item.get("eqSize", "")),
                _safe_str(first_item.get("eqHeight", "")),
                _safe_str(first_item.get("eqIsoGroup", "")),
            ]
            if part
        ]
    )
    if size and not _safe_str(load_row.get("Size", "")):
        updates["Size"] = size

    summary = {
        "Booking": booking,
        "Line": booking_record.get("lineId", ""),
        "Visit": get_nested(booking_record, "visit.visitId"),
        "POL": booking_record.get("polId", ""),
        "POD": booking_record.get("pod1Id", ""),
        "Earliest": booking_record.get("earliestDate", ""),
        "Latest": booking_record.get("latestDate", ""),
        "Quantity": booking_record.get("quantity", ""),
        "Tally": booking_record.get("tally", ""),
    }
    updates["Dispatcher Notes"] = _append_port_houston_notes(load_row.get("Dispatcher Notes", ""), summary)
    return updates


def _apply_port_houston_updates(load_id: int, updates: dict, action_type: str, principal: AuthenticatedActor) -> None:
    apply_port_houston_data(actor=principal, load_id=load_id, updates=updates, action_type=action_type)


def _update_load_columns_if_present(load_id: int, updates: dict) -> list[str]:
    existing_columns = _existing_load_columns()
    safe_updates = {
        column: value
        for column, value in (updates or {}).items()
        if column in existing_columns and column not in {"id", "created_at", "updated_at"}
    }
    if not safe_updates:
        return []
    set_clause = ", ".join([f"{column} = :{column}" for column in safe_updates])
    params = dict(safe_updates)
    params["load_id"] = int(load_id)
    execute(
        f"""
        update loads
        set {set_clause},
            updated_at = now()
        where id = :load_id
        """,
        params,
    )
    return list(safe_updates.keys())


def _port_houston_core_updates_from_records(load_row: dict, unit_record: dict | None, booking_record: dict | None) -> tuple[dict, dict]:
    core_updates: dict = {}
    extra_updates: dict = {}
    notes = _safe_str(load_row.get("Dispatcher Notes", ""))

    if unit_record:
        summary = summarize_unit(unit_record)
        if _safe_str(summary.get("Container", "")) and not _safe_str(load_row.get("Container Number", "")):
            core_updates["Container Number"] = summary["Container"]
        if _safe_str(summary.get("Size", "")) and not _safe_str(load_row.get("Size", "")):
            core_updates["Size"] = summary["Size"]
        if _safe_str(summary.get("Facility", "")) and not _safe_str(load_row.get("Port", "")):
            core_updates["Port"] = summary["Facility"]
        if _safe_str(summary.get("Line", "")):
            extra_updates["steamship_line"] = summary["Line"]
        if _safe_str(summary.get("Facility", "")):
            extra_updates["terminal"] = summary["Facility"]
        if _safe_str(summary.get("Return Location", "")):
            extra_updates["empty_return_location"] = summary["Return Location"]
        if _safe_str(summary.get("Position", "")):
            extra_updates["current_location"] = summary["Position"]
        notes = _append_port_houston_notes(notes, summary)

    if booking_record:
        booking = _safe_str(booking_record.get("nbr", ""))
        if booking and not _safe_str(load_row.get("Booking Number", "")):
            core_updates["Booking Number"] = booking
        client_ref = _safe_str(booking_record.get("clientRefNo", ""))
        if client_ref and not _safe_str(load_row.get("Reference Number", "")):
            core_updates["Reference Number"] = client_ref
        if _safe_str(booking_record.get("destination", "")) and not _safe_str(load_row.get("Warehouse", "")):
            core_updates["Warehouse"] = _safe_str(booking_record.get("destination", ""))

        first_item = {}
        items = booking_record.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            first_item = items[0]
        size = " ".join(
            [
                part
                for part in [
                    _safe_str(first_item.get("eqSize", "")),
                    _safe_str(first_item.get("eqHeight", "")),
                    _safe_str(first_item.get("eqIsoGroup", "")),
                ]
                if part
            ]
        )
        if size and not _safe_str(load_row.get("Size", "")):
            core_updates["Size"] = size
        if _safe_str(booking_record.get("lineId", "")):
            extra_updates["steamship_line"] = _safe_str(booking_record.get("lineId", ""))
        if _safe_str(get_nested(booking_record, "visit.visitId")):
            extra_updates["vessel_name"] = _safe_str(get_nested(booking_record, "visit.visitId"))
        if _safe_str(booking_record.get("latestDate", "")) and not _safe_str(load_row.get("Document Cutoff", "")):
            core_updates["Document Cutoff"] = _safe_str(booking_record.get("latestDate", ""))

        summary = {
            "Booking": booking,
            "Line": booking_record.get("lineId", ""),
            "Visit": get_nested(booking_record, "visit.visitId"),
            "POL": booking_record.get("polId", ""),
            "POD": booking_record.get("pod1Id", ""),
            "Earliest": booking_record.get("earliestDate", ""),
            "Latest": booking_record.get("latestDate", ""),
            "Quantity": booking_record.get("quantity", ""),
            "Tally": booking_record.get("tally", ""),
        }
        notes = _append_port_houston_notes(notes, summary)

    if unit_record or booking_record:
        core_updates["Dispatcher Notes"] = notes
        if _safe_str(load_row.get("Status", "")) in {"Booking Verified", "Awaiting Appointment"}:
            core_updates["Status"] = "Port Verified"

    return core_updates, extra_updates


def _render_load_port_houston_panel(selected_load, readiness: dict, principal: AuthenticatedActor | None = None) -> None:
    load_id = int(selected_load["_row_id"])
    default_container = _safe_str(selected_load.get("Container Number", ""))
    default_booking = _safe_str(selected_load.get("Booking Number", ""))

    st.markdown("### Port Sync")
    st.caption("Sync Port Houston data after the order is created and before dispatch. This keeps terminal, availability, LFD/return notes, and appointment context attached to the load.")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Port Verified", "Yes" if readiness.get("port_verified") else "No")
    p2.metric("Terminal", _first_present(selected_load, ["terminal", "Port"], "-"))
    p3.metric("PIN / Appt", _load_pin_display(selected_load))
    p4.metric("Next Action", readiness.get("next_action", "-"))

    c1, c2 = st.columns(2)
    container_value = c1.text_input("Container", value=default_container, key=f"load_port_sync_container_{load_id}")
    booking_value = c2.text_input("Booking", value=default_booking, key=f"load_port_sync_booking_{load_id}")

    _can_apply_port_data = has_permission(principal, Permission.PORT_DATA_APPLY) if principal else False
    if not _can_apply_port_data:
        st.caption("Your role does not have permission to apply Port Houston data to loads.")
    if st.button("Sync Port Data", key=f"load_port_sync_{load_id}", use_container_width=True, disabled=not _can_apply_port_data):
        if not container_value.strip() and not booking_value.strip():
            st.error("Container or booking is required for Port Houston sync.")
        else:
            client = _get_port_houston_client_or_none()
            if client:
                unit_record = None
                booking_record = None
                errors = []
                if container_value.strip():
                    try:
                        unit_data = client.get_inventory_units(container=container_value)
                        unit_records = content_records(unit_data)
                        unit_record = unit_records[0] if unit_records else None
                        _store_port_houston_result(f"load_port_unit_result_{load_id}", unit_data, "Container / Unit", container_value, load_id)
                    except Exception as exc:
                        errors.append(f"Container lookup failed: {exc}")
                        _log_port_houston_event(action_type="lookup", lookup_type="Container / Unit", request_reference=container_value, load_id=load_id, status="failed", error_message=str(exc))
                if booking_value.strip():
                    try:
                        booking_data = client.get_bookings(booking=booking_value)
                        booking_records = content_records(booking_data)
                        booking_record = booking_records[0] if booking_records else None
                        _store_port_houston_result(f"load_port_booking_result_{load_id}", booking_data, "Booking", booking_value, load_id)
                    except Exception as exc:
                        errors.append(f"Booking lookup failed: {exc}")
                        _log_port_houston_event(action_type="lookup", lookup_type="Booking", request_reference=booking_value, load_id=load_id, status="failed", error_message=str(exc))

                core_updates, extra_updates = _port_houston_core_updates_from_records(selected_load, unit_record, booking_record)
                updated_fields = []
                try:
                    require_permission(principal, Permission.PORT_DATA_APPLY)
                    if core_updates:
                        DispatchDatabaseClient().update_row_fields(load_id, core_updates, created_by=principal.actor)
                        updated_fields.extend(core_updates.keys())
                    updated_fields.extend(apply_port_houston_extra_columns(actor=principal, load_id=load_id, updates=extra_updates))
                except AuthorizationError as exc:
                    st.error(str(exc))
                    return

                _log_port_houston_event(
                    action_type="load_port_sync",
                    lookup_type="Container / Booking",
                    request_reference=container_value or booking_value,
                    load_id=load_id,
                    status="failed" if errors and not updated_fields else "success",
                    error_message="; ".join(errors),
                    response_summary={"updated_fields": updated_fields, "unit_found": bool(unit_record), "booking_found": bool(booking_record)},
                )
                if errors:
                    st.warning("; ".join(errors))
                if updated_fields:
                    refresh_data()
                    st.success("Port data synced. Updated: " + ", ".join(updated_fields))
                    st.rerun()
                elif not errors:
                    st.info("Port Houston returned no matching container or booking records.")

    unit_records = _render_port_houston_result(f"load_port_unit_result_{load_id}", mode="unit")
    booking_records = _render_port_houston_result(f"load_port_booking_result_{load_id}")

    st.divider()
    st.markdown("### Appointment / PIN")
    pin_requirements = []
    if not _status_at_or_after(_safe_str(selected_load.get("Status", "")), "Booking Verified"):
        pin_requirements.append("booking verified")
    if not readiness.get("port_verified"):
        pin_requirements.append("port verified")
    if not _load_has_driver(selected_load):
        pin_requirements.append("driver assigned")
    if not _load_has_truck(selected_load):
        pin_requirements.append("truck assigned")
    if not _first_present(selected_load, ["Port", "terminal"], ""):
        pin_requirements.append("terminal confirmed")
    if not _first_present(selected_load, ["Delivery Need Date", "delivery_need_date"], ""):
        pin_requirements.append("pickup/delivery date")
    if pin_requirements:
        st.warning("Before requesting PIN/appointment: " + ", ".join(pin_requirements))
    else:
        st.success("Ready for Port PIN / appointment request.")

    pin_c1, pin_c2, pin_c3 = st.columns(3)
    pin_driver = pin_c1.text_input("Driver", value=_safe_str(selected_load.get("Driver Name", "")), key=f"load_pin_driver_{load_id}")
    pin_truck = pin_c2.text_input("Truck License / Truck #", value=_safe_str(selected_load.get("Truck Assigned", "")), key=f"load_pin_truck_{load_id}")
    pin_chassis = pin_c3.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")), key=f"load_pin_chassis_{load_id}")

    pin_d1, pin_d2, pin_d3 = st.columns(3)
    pin_tran_label = pin_d1.selectbox("Transaction Type", list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()), key=f"load_pin_tran_type_{load_id}")
    pin_date = pin_d2.date_input("Requested Date", value=date.today(), key=f"load_pin_date_{load_id}")
    pin_time = pin_d3.selectbox("Requested Time", ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"], key=f"load_pin_time_{load_id}")

    pin_g1, pin_g2, pin_g3 = st.columns(3)
    pin_gate = pin_g1.selectbox("Gate", ["BPT MAIN", "BCT MAIN"], key=f"load_pin_gate_{load_id}")
    pin_scac = pin_g2.text_input("Trucking Company / SCAC", value=_get_app_setting("PORT_HOUSTON_OPERATOR", "POHA"), key=f"load_pin_scac_{load_id}")
    pin_confirmation = pin_g3.text_input("PIN / Appointment #", value="", key=f"load_pin_confirmation_{load_id}")
    pin_equipment_type = st.text_input("Equipment Type", value=_safe_str(selected_load.get("Size", "")) or "40HC", key=f"load_pin_equipment_{load_id}")

    pin_payload = _build_port_houston_appointment_payload(
        action="Create",
        appointment_nbr=pin_confirmation,
        appointment_date=pin_date,
        appointment_time=pin_time,
        gate_id=pin_gate,
        truck_license=pin_truck,
        trucking_co_id=pin_scac,
        tran_type=PORT_HOUSTON_APPOINTMENT_TRAN_TYPES[pin_tran_label],
        container=container_value,
        booking=booking_value,
        chassis=pin_chassis,
        equipment_type=pin_equipment_type,
        owns_chassis=True,
    )
    with st.expander("Review PIN / Appointment Payload", expanded=False):
        st.text_area("Payload", value=pin_payload, height=240, key=f"load_pin_payload_{load_id}")

    pin_save_requirements = []
    if not _status_at_or_after(_safe_str(selected_load.get("Status", "")), "Booking Verified"):
        pin_save_requirements.append("booking verified")
    if not readiness.get("port_verified"):
        pin_save_requirements.append("port verified")
    if not pin_driver.strip():
        pin_save_requirements.append("driver")
    if not pin_truck.strip():
        pin_save_requirements.append("truck")
    if not _first_present(selected_load, ["Port", "terminal"], ""):
        pin_save_requirements.append("terminal")
    if not _first_present(selected_load, ["Delivery Need Date", "delivery_need_date"], ""):
        pin_save_requirements.append("pickup/delivery date")

    if st.button("Save PIN / Appointment To Load", key=f"load_save_pin_{load_id}", use_container_width=True, disabled=not _can_apply_port_data):
        if pin_save_requirements:
            st.error("Cannot save PIN / appointment until these items are complete: " + ", ".join(pin_save_requirements))
        elif not booking_value and not container_value:
            st.error("Booking or container is required.")
        elif not pin_truck.strip():
            st.error("Truck license / truck number is required.")
        else:
            target_status = "PIN Received" if pin_confirmation.strip() else "Ready for Appointment / PIN"
            appointment_value = pd.Timestamp.combine(pin_date, pd.to_datetime(pin_time).time()).to_pydatetime()
            note = (
                "\n\nPort Houston PIN / Appointment:"
                f"\nTransaction Type: {pin_tran_label}"
                f"\nDate/Time: {pin_date} {pin_time}"
                f"\nGate: {pin_gate}"
                f"\nPIN / Appointment #: {pin_confirmation.strip() or 'Pending'}"
                f"\nBooking: {booking_value}"
                f"\nContainer: {container_value}"
                f"\nTruck: {pin_truck}"
                f"\nDriver: {pin_driver}"
                f"\nChassis: {pin_chassis}"
            )
            try:
                require_permission(principal, Permission.PORT_DATA_APPLY)
                DispatchDatabaseClient().update_row_fields(
                    load_id,
                    {
                        "Status": target_status,
                        "Driver Name": pin_driver.strip(),
                        "Truck Assigned": pin_truck.strip(),
                        "Chassis": pin_chassis.strip(),
                        "Dispatcher Notes": (_safe_str(selected_load.get("Dispatcher Notes", "")) + note).strip(),
                    },
                    created_by=principal.actor,
                )
                updated_extra = apply_port_houston_extra_columns(
                    actor=principal,
                    load_id=load_id,
                    updates={
                        "pickup_reference": pin_confirmation.strip() or None,
                        "pickup_appointment": appointment_value,
                    },
                )
            except AuthorizationError as exc:
                st.error(str(exc))
                return
            _log_port_houston_event(
                action_type="pin_appointment_saved",
                lookup_type="Express Pass / PIN",
                request_reference=booking_value or container_value,
                load_id=load_id,
                response_summary={
                    "status": target_status,
                    "transaction_type": pin_tran_label,
                    "date": str(pin_date),
                    "time": pin_time,
                    "gate": pin_gate,
                    "pin": pin_confirmation.strip(),
                    "payload": pin_payload,
                    "updated_extra": updated_extra,
                },
            )
            refresh_data()
            st.success("PIN / appointment details saved to the load.")
            st.rerun()


def _xml_escape(value) -> str:
    text = _safe_str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_port_houston_appointment_payload(
    *,
    action: str,
    appointment_nbr: str,
    appointment_date,
    appointment_time: str,
    gate_id: str,
    truck_license: str,
    trucking_co_id: str,
    tran_type: str,
    container: str,
    booking: str,
    chassis: str,
    equipment_type: str,
    owns_chassis: bool,
) -> str:
    appointment_date_text = appointment_date.strftime("%Y-%m-%d") if hasattr(appointment_date, "strftime") else _safe_str(appointment_date)
    chassis_owner_text = "true" if owns_chassis else "false"
    action_tag = {"Create": "create-appointment", "Update": "update-appointment", "Cancel": "cancel-appointment"}.get(action, "create-appointment")
    lines = ["<gate>", f"  <{action_tag}>"]
    if action in ["Update", "Cancel"]:
        lines.append(f"    <appointment-nbr>{_xml_escape(appointment_nbr)}</appointment-nbr>")
    if action != "Cancel":
        lines.extend(
            [
                f"    <appointment-date>{_xml_escape(appointment_date_text)}</appointment-date>",
                f"    <appointment-time>{_xml_escape(appointment_time)}</appointment-time>",
                f"    <gate-id>{_xml_escape(gate_id)}</gate-id>",
                f"    <truck license-nbr=\"{_xml_escape(truck_license)}\" trucking-co-id=\"{_xml_escape(trucking_co_id)}\" />",
                f"    <tran-type>{_xml_escape(tran_type)}</tran-type>",
            ]
        )
        if booking:
            lines.append(
                f"    <eq-order order-nbr=\"{_xml_escape(booking)}\"><eq-order-items>"
                f"<eq-order-item type=\"{_xml_escape(equipment_type)}\" />"
                f"</eq-order-items></eq-order>"
            )
        if container:
            container_attr = f"eqid=\"{_xml_escape(container)}\"" if tran_type in ["DI", "DE", "RE"] else f"type=\"{_xml_escape(equipment_type)}\""
            lines.append(f"    <container {container_attr} />")
        if chassis:
            lines.append(f"    <chassis eqid=\"{_xml_escape(chassis)}\" is-owners=\"{chassis_owner_text}\" />")
        elif tran_type == "DC":
            lines.append(f"    <chassis type=\"{_xml_escape(equipment_type)}\" />")
    lines.extend([f"  </{action_tag}>", "</gate>"])
    return "\n".join(lines)


def _render_port_houston_setup() -> None:
    settings = get_port_houston_settings()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Configured", "Yes" if settings.is_configured else "No")
    c2.metric("Operator", settings.operator or "-")
    c3.metric("API Base", "Set" if settings.base_url else "Missing")
    c4.metric("Timeout", f"{settings.timeout_seconds}s")

    if settings.missing:
        st.warning("Missing settings: " + ", ".join(settings.missing))
        st.caption("Add these to `.env` or Streamlit secrets. Do not put Port Houston credentials in source code.")
    else:
        st.success("Port Houston credentials are available from local settings.")

    with st.expander("Connection Settings", expanded=False):
        st.write(
            {
                "PORT_HOUSTON_BASE_URL": settings.base_url,
                "PORT_HOUSTON_AUTH_URL": settings.auth_url,
                "PORT_HOUSTON_CLIENT_ID": _redacted_config_value(settings.client_id),
                "PORT_HOUSTON_CLIENT_SECRET": _redacted_config_value(settings.client_secret),
                "PORT_HOUSTON_OPERATOR": settings.operator,
            }
        )

    if st.button("Test Port Houston Connection", use_container_width=True, disabled=not settings.is_configured):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                client.get_token(force_refresh=True)
                _log_port_houston_event(action_type="token_test")
                st.success("Connection test passed. Token was received and cached for this session.")
            except Exception as exc:
                _log_port_houston_event(action_type="token_test", status="failed", error_message=str(exc))
                st.error(f"Connection test failed: {exc}")


def _render_port_houston_selected_load(df: pd.DataFrame, principal: AuthenticatedActor) -> None:
    st.markdown("#### Load Lookup and Sync")
    st.caption("Pull Port Houston unit or booking data for a TMS load and update safe fields/notes.")
    load_options = _port_houston_load_options(df)
    if not load_options:
        st.info("No active loads are available for Port Houston lookup.")
        return

    selected_load = st.selectbox("Select Load", load_options, format_func=_port_houston_load_label, key="port_houston_selected_load")
    load_id = int(selected_load["_row_id"])
    default_container = _safe_str(selected_load.get("Container Number", ""))
    default_booking = _safe_str(selected_load.get("Booking Number", ""))

    l1, l2, l3, l4 = st.columns(4)
    l1.metric("Booking", default_booking or "-")
    l2.metric("Container", default_container or "-")
    l3.metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    l4.metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    container_value = st.text_input("Container to Check", value=default_container, key="port_houston_load_container")
    booking_value = st.text_input("Booking to Check", value=default_booking, key="port_houston_load_booking")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("Lookup Container", key="port_houston_lookup_load_container", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_inventory_units(container=container_value)
                    _store_port_houston_result("port_houston_load_unit_result", data, "Container / Unit", container_value, load_id)
                    st.success("Container lookup complete.")
                except Exception as exc:
                    _log_port_houston_event(action_type="lookup", lookup_type="Container / Unit", request_reference=container_value, load_id=load_id, status="failed", error_message=str(exc))
                    st.error(f"Container lookup failed: {exc}")
    with b2:
        if st.button("Lookup Booking", key="port_houston_lookup_load_booking", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_bookings(booking=booking_value)
                    _store_port_houston_result("port_houston_load_booking_result", data, "Booking", booking_value, load_id)
                    st.success("Booking lookup complete.")
                except Exception as exc:
                    _log_port_houston_event(action_type="lookup", lookup_type="Booking", request_reference=booking_value, load_id=load_id, status="failed", error_message=str(exc))
                    st.error(f"Booking lookup failed: {exc}")

    can_apply = has_permission(principal, Permission.PORT_DATA_APPLY)
    if not can_apply:
        st.caption("Your role does not have permission to apply Port Houston data to loads.")

    unit_records = _render_port_houston_result("port_houston_load_unit_result", mode="unit")
    if unit_records and st.button("Update Load From Container Data", key="port_houston_update_from_unit", use_container_width=True, disabled=not can_apply):
        updates = _updates_from_port_houston_unit(selected_load, unit_records[0])
        try:
            _apply_port_houston_updates(load_id, updates, "update_load_from_unit", principal)
        except AuthorizationError as exc:
            st.error(str(exc))
        else:
            refresh_data()
            st.success("Load updated from Port Houston container data.")
            st.rerun()

    booking_records = _render_port_houston_result("port_houston_load_booking_result")
    if booking_records and st.button("Update Load From Booking Data", key="port_houston_update_from_booking", use_container_width=True, disabled=not can_apply):
        updates = _updates_from_port_houston_booking(selected_load, booking_records[0])
        try:
            _apply_port_houston_updates(load_id, updates, "update_load_from_booking", principal)
        except AuthorizationError as exc:
            st.error(str(exc))
        else:
            refresh_data()
            st.success("Load updated from Port Houston booking data.")
            st.rerun()
        st.divider()
    st.markdown("#### Express Pass / PIN Request")

    pin_c1, pin_c2, pin_c3 = st.columns(3)

    with pin_c1:
        pin_driver = st.text_input(
            "Driver",
            value=_safe_str(selected_load.get("Driver Name", "")),
            key=f"pin_driver_{load_id}",
        )

    with pin_c2:
        pin_truck = st.text_input(
            "Truck License / Truck #",
            value=_safe_str(selected_load.get("Truck Assigned", "")),
            key=f"pin_truck_{load_id}",
        )

    with pin_c3:
        pin_chassis = st.text_input(
            "Chassis",
            value=_safe_str(selected_load.get("Chassis", "")),
            key=f"pin_chassis_{load_id}",
        )

    pin_tran_label = st.selectbox(
        "Port Transaction Type",
        list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()),
        key=f"pin_tran_type_{load_id}",
    )

    pin_date = st.date_input(
        "Requested Date",
        value=date.today(),
        key=f"pin_date_{load_id}",
    )

    pin_time = st.selectbox(
        "Requested Time",
        ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"],
        key=f"pin_time_{load_id}",
    )

    pin_gate = st.selectbox(
        "Gate",
        ["BPT MAIN", "BCT MAIN"],
        key=f"pin_gate_{load_id}",
    )

    pin_scac = st.text_input(
        "Trucking Company / SCAC",
        value=_get_app_setting("PORT_HOUSTON_OPERATOR", "POHA"),
        key=f"pin_scac_{load_id}",
    )

    pin_equipment_type = st.text_input(
        "Equipment Type",
        value=_safe_str(selected_load.get("Size", "")) or "40HC",
        key=f"pin_equipment_{load_id}",
    )

    pin_payload = _build_port_houston_appointment_payload(
        action="Create",
        appointment_nbr="",
        appointment_date=pin_date,
        appointment_time=pin_time,
        gate_id=pin_gate,
        truck_license=pin_truck,
        trucking_co_id=pin_scac,
        tran_type=PORT_HOUSTON_APPOINTMENT_TRAN_TYPES[pin_tran_label],
        container=container_value,
        booking=booking_value,
        chassis=pin_chassis,
        equipment_type=pin_equipment_type,
        owns_chassis=True,
    )

    with st.expander("Review Port Houston PIN / Appointment Payload", expanded=False):
        st.text_area(
            "Payload",
            value=pin_payload,
            height=260,
            key=f"pin_payload_{load_id}",
        )

    if st.button("Save PIN Request To Load", key=f"save_pin_request_{load_id}", use_container_width=True, disabled=not has_permission(principal, Permission.PORT_DATA_APPLY)):
        if not booking_value and not container_value:
            st.error("Booking or container is required.")
        elif not pin_truck.strip():
            st.error("Truck license / truck number is required.")
        else:
            try:
                require_permission(principal, Permission.PORT_DATA_APPLY)
            except AuthorizationError as exc:
                st.error(str(exc))
                return
            execute(
                """
                update loads
                set dispatcher_notes = concat(
                    coalesce(dispatcher_notes, ''),
                    E'\n\nPort Houston PIN / Express Pass Request:',
                    E'\nTransaction Type: ', :tran_type,
                    E'\nDate/Time: ', :pin_date, ' ', :pin_time,
                    E'\nGate: ', :gate,
                    E'\nBooking: ', :booking,
                    E'\nContainer: ', :container,
                    E'\nTruck: ', :truck,
                    E'\nDriver: ', :driver,
                    E'\nChassis: ', :chassis
                )
                where id = :load_id
                """,
                {
                    "load_id": load_id,
                    "tran_type": pin_tran_label,
                    "pin_date": str(pin_date),
                    "pin_time": pin_time,
                    "gate": pin_gate,
                    "booking": booking_value,
                    "container": container_value,
                    "truck": pin_truck,
                    "driver": pin_driver,
                    "chassis": pin_chassis,
                },
            )

            _log_port_houston_event(
                action_type="pin_request_saved",
                lookup_type="Express Pass / PIN",
                request_reference=booking_value or container_value,
                load_id=load_id,
                response_summary={
                    "transaction_type": pin_tran_label,
                    "date": str(pin_date),
                    "time": pin_time,
                    "gate": pin_gate,
                    "booking": booking_value,
                    "container": container_value,
                    "truck": pin_truck,
                    "driver": pin_driver,
                    "chassis": pin_chassis,
                    "payload": pin_payload,
                },
            )

            refresh_data()
            st.success("PIN request saved to load notes and Port Houston log.")
            st.rerun()
    
def _render_port_houston_direct_lookup() -> None:
    st.markdown("#### Live Endpoint Lookup")
    endpoint_name = st.selectbox("Data Type", list(PORT_HOUSTON_ENDPOINTS.keys()), key="port_houston_endpoint_name")
    endpoint = PORT_HOUSTON_ENDPOINTS[endpoint_name]
    st.caption(endpoint["hint"])

    c1, c2 = st.columns(2)
    reference = c1.text_input("Quick Reference", placeholder="Container, booking, or vessel visit", key="port_houston_reference")
    predicate = c2.text_input("Predicate", placeholder="Example: routing.pod1Id=TWKHH", key="port_houston_predicate")
    fields = st.text_area("Fields", value=endpoint["fields"], height=90, key=f"port_houston_fields_{endpoint_name}")

    if st.button("Run Lookup", key="port_houston_direct_lookup", use_container_width=True):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                if endpoint_name == "Container / Unit":
                    data = client.get_inventory_units(container=reference, predicate=predicate, fields=fields or UNIT_FIELDS)
                elif endpoint_name == "Booking":
                    data = client.get_bookings(booking=reference, predicate=predicate, fields=fields or BOOKING_FIELDS)
                elif endpoint_name == "Vessel Visit":
                    data = client.get_vessel_visits(visit_id=reference, predicate=predicate, fields=fields or VESSEL_FIELDS)
                elif endpoint_name == "Gate Appointments":
                    data = client.get_gate_appointments(predicate=predicate)
                elif endpoint_name == "Appointment Time Slots":
                    data = client.get_appointment_time_slots(predicate=predicate)
                else:
                    params = {}
                    if predicate.strip():
                        params["predicate"] = predicate.strip()
                    if fields.strip():
                        params["fields"] = fields.strip()
                    data = client.request(endpoint["endpoint"], params=params)
                _store_port_houston_result("port_houston_direct_result", data, endpoint_name, reference or predicate)
                st.success("Lookup complete.")
            except Exception as exc:
                _log_port_houston_event(action_type="lookup", lookup_type=endpoint_name, request_reference=reference or predicate, status="failed", error_message=str(exc))
                st.error(f"Lookup failed: {exc}")

    _render_port_houston_result("port_houston_direct_result", mode="unit" if endpoint_name == "Container / Unit" else "flat")


def _render_port_houston_appointments(df: pd.DataFrame) -> None:
    st.markdown("#### Appointment Tools")
    st.caption("Build Port Houston appointment payloads from a load. Live appointment creation also requires N4 authorization from Port Houston.")

    load_options = _port_houston_load_options(df)
    selected_load = None
    if load_options:
        selected_load = st.selectbox("Use Load Defaults", load_options, format_func=_port_houston_load_label, key="port_houston_appt_load")

    default_container = _safe_str(selected_load.get("Container Number", "")) if selected_load else ""
    default_booking = _safe_str(selected_load.get("Booking Number", "")) if selected_load else ""
    default_chassis = _safe_str(selected_load.get("Chassis", "")) if selected_load else ""
    default_size = _safe_str(selected_load.get("Size", "")) if selected_load else "40HC"

    a1, a2, a3 = st.columns(3)
    action = a1.selectbox("Action", ["Create", "Update", "Cancel"], key="port_houston_appt_action")
    appointment_nbr = a2.text_input("Appointment Number", key="port_houston_appt_nbr")
    tran_label = a3.selectbox("Transaction Type", list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()), key="port_houston_appt_tran")

    d1, d2, d3, d4 = st.columns(4)
    appointment_date = d1.date_input("Appointment Date", value=date.today(), key="port_houston_appt_date")
    appointment_time = d2.selectbox(
        "Arrival Hour",
        ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"],
        key="port_houston_appt_time",
    )
    gate_id = d3.selectbox("Gate", ["BPT MAIN", "BCT MAIN"], key="port_houston_appt_gate")
    owns_chassis = d4.checkbox("Driver brings/owns chassis", value=True, key="port_houston_appt_owns_chassis")

    f1, f2, f3 = st.columns(3)
    truck_license = f1.text_input("Truck License", placeholder="LP12345 or SCAC if unknown", key="port_houston_appt_truck")
    trucking_co_id = f2.text_input("Trucking Company / SCAC", key="port_houston_appt_scac")
    equipment_type = f3.text_input("Equipment Type", value=default_size or "40HC", key="port_houston_appt_equipment")

    c1, c2, c3 = st.columns(3)
    container = c1.text_input("Container", value=default_container, key="port_houston_appt_container")
    booking = c2.text_input("Booking / Order", value=default_booking, key="port_houston_appt_booking")
    chassis = c3.text_input("Chassis", value=default_chassis, key="port_houston_appt_chassis")

    payload = _build_port_houston_appointment_payload(
        action=action,
        appointment_nbr=appointment_nbr,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        gate_id=gate_id,
        truck_license=truck_license,
        trucking_co_id=trucking_co_id,
        tran_type=PORT_HOUSTON_APPOINTMENT_TRAN_TYPES[tran_label],
        container=container,
        booking=booking,
        chassis=chassis,
        equipment_type=equipment_type,
        owns_chassis=owns_chassis,
    )
    st.text_area("Appointment SOAP Payload", value=payload, height=280, key="port_houston_appt_payload")
    st.download_button("Download Appointment Payload", data=payload, file_name=f"port_houston_{action.lower()}_appointment.xml", mime="application/xml", use_container_width=True)

    predicate = st.text_input("Time Slot Predicate", placeholder="Optional field filter", key="port_houston_timeslot_predicate")
    if st.button("Check Appointment Time Slots", key="port_houston_check_timeslots", use_container_width=True):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                data = client.get_appointment_time_slots(predicate=predicate)
                _store_port_houston_result("port_houston_timeslot_result", data, "Appointment Time Slots", predicate)
                st.success("Time slot lookup complete.")
            except Exception as exc:
                st.error(f"Time slot lookup failed: {exc}")
    _render_port_houston_result("port_houston_timeslot_result")


def _render_port_houston_subscriptions() -> None:
    st.markdown("#### Event Subscriptions")
    st.caption("Create or review Navis EVP event subscriptions for booking changes, gate events, units, and vessel updates.")

    s1, s2, s3 = st.columns(3)
    event_name = s1.selectbox("Event", PORT_HOUSTON_SUBSCRIPTION_EVENTS, key="port_houston_sub_event")
    operation = s2.selectbox("Operation", ["", "create", "update", "delete"], key="port_houston_sub_operation")
    persistence = s3.checkbox("Persistent", value=True, key="port_houston_sub_persistent")

    group_default = f"Calitrans{event_name}{datetime.now().strftime('%Y%m%d')}"
    group_id = st.text_input("Group ID", value=group_default, key="port_houston_sub_group")
    predicate = st.text_input("Subscription Predicate", placeholder="Example: unitId=ABCD1234567 or freightKind=FCL", key="port_houston_sub_predicate")
    fields = st.text_area("Fields to Include", value="", placeholder="Comma-separated field list, optional", height=80, key="port_houston_sub_fields")

    filter_payload = {"eventName": event_name}
    if operation:
        filter_payload["operation"] = operation
    if predicate.strip() or fields.strip():
        filter_payload["filter"] = {}
        if predicate.strip():
            filter_payload["filter"]["predicate"] = predicate.strip()
        if fields.strip():
            filter_payload["filter"]["fields"] = [field.strip() for field in fields.split(",") if field.strip()]

    payload = {"groupId": group_id, "persistence": persistence, "transport": "ws", "filters": [filter_payload]}
    st.json(payload)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("List Subscriptions", key="port_houston_list_subscribers", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_subscribers()
                    _store_port_houston_result("port_houston_subscribers_result", data, "Subscriptions", "")
                    st.success("Subscriptions loaded.")
                except Exception as exc:
                    st.error(f"Could not load subscriptions: {exc}")
    with c2:
        if st.button("Create Subscription", key="port_houston_create_subscriber", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.create_subscriber(payload)
                    _store_port_houston_result("port_houston_subscribers_result", data, "Create Subscription", group_id)
                    st.success("Subscription request sent.")
                except Exception as exc:
                    st.error(f"Could not create subscription: {exc}")

    records = _render_port_houston_result("port_houston_subscribers_result")
    if records:
        st.info("For websocket monitoring, connect to the documented stream URL with the returned subscription id and groupId.")


def _render_port_houston_mapping() -> None:
    st.markdown("#### Drayage Mapping")
    st.caption("Recommended Port Houston EVP data mapping for CaliTrans TMS.")
    rows = [
        {"EVP Area": "Inventory Unit", "Endpoint": "/inventory/units", "TMS Use": "Container status, position, yard/facility, routing, return location", "TMS Action": "Update load notes, container size, port/facility, and availability checks"},
        {"EVP Area": "Booking", "Endpoint": "/orders/bookings", "TMS Use": "Booking changes, quantity/tally, line, vessel visit, receiving window", "TMS Action": "Update booking review, dispatcher notes, and avoid dry runs"},
        {"EVP Area": "Vessel Visit", "Endpoint": "/vessel/vesselvisits", "TMS Use": "ETA/ETD, begin receive, cutoff, first availability", "TMS Action": "Drive appointment planning and exception alerts"},
        {"EVP Area": "Gate Appointments", "Endpoint": "/road/gateappointments", "TMS Use": "Existing appointment visibility", "TMS Action": "Confirm appointment state before dispatch"},
        {"EVP Area": "Gate Transactions", "Endpoint": "/road/gatetransactions", "TMS Use": "Ingate/outgate and trouble stages", "TMS Action": "Update dispatch timeline and customer status"},
        {"EVP Area": "Notify Subscriptions", "Endpoint": "/notify/subscribers", "TMS Use": "Booking, unit, appointment, and gate event monitoring", "TMS Action": "Future automation feed for Operations Inbox alerts"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Required Local Settings", expanded=False):
        st.code(
            "\n".join(
                [
                    "PORT_HOUSTON_CLIENT_ID=your_client_id",
                    "PORT_HOUSTON_CLIENT_SECRET=your_client_secret",
                    "PORT_HOUSTON_OPERATOR=POHA",
                    "PORT_HOUSTON_BASE_URL=https://api.america.naviscloudops.com/v3/evp",
                    "PORT_HOUSTON_AUTH_URL=https://auth-v1.america.naviscloudops.com/auth/realms/phaprod/protocol/openid-connect/token",
                ]
            ),
            language="bash",
        )


def render_port_houston_integration(df: pd.DataFrame, principal: AuthenticatedActor) -> None:
    st.subheader("Port Houston Integration")
    st.caption("All-in-one Navis EVP workspace for Port Houston container, booking, vessel, gate, appointment, and subscription data.")
    _render_port_houston_setup()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Load Sync", "Live Lookup", "Appointments", "Subscriptions", "Data Map"])
    with tab1:
        _render_port_houston_selected_load(df, principal)
    with tab2:
        _render_port_houston_direct_lookup()
    with tab3:
        _render_port_houston_appointments(df)
    with tab4:
        _render_port_houston_subscriptions()
    with tab5:
        _render_port_houston_mapping()


render_load_port_houston_panel = _render_load_port_houston_panel
