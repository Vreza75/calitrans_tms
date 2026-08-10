from __future__ import annotations

import pandas as pd
import streamlit as st

from db_client import execute
import services.operations_case_service as case_service
import services.operations_inbox_service as ops

OPERATIONS_CASE_STATUSES = case_service.OPERATIONS_CASE_STATUSES
OPERATIONS_CASE_OWNERS = case_service.OPERATIONS_CASE_OWNERS
OPERATIONS_CASE_PRIORITIES = case_service.OPERATIONS_CASE_PRIORITIES

_safe_str = case_service.safe_str
_int_or_none = case_service.int_or_none
_case_customer_from_sender = case_service.case_customer_from_sender
_load_operations_case_email_summary = case_service.load_operations_case_email_summary
_format_short_timestamp = case_service.format_short_timestamp
_format_relative_timestamp = case_service.format_relative_timestamp
_format_case_sla_label = case_service.format_case_sla_label
_load_operations_case_timeline = case_service.load_operations_case_timeline
_load_recent_operations_cases = case_service.load_recent_operations_cases
_load_operations_case_owner_history = case_service.load_operations_case_owner_history
_merge_operations_cases = case_service.merge_operations_cases
_set_operations_case_status = case_service.set_operations_case_status
_update_operations_case = case_service.update_operations_case
_add_operations_case_note = case_service.add_operations_case_note
refresh_data = ops.refresh_data

def _render_operations_case_summary_header(
    *,
    operations_case: dict,
    record,
    parsed: dict,
    tokens: dict,
    matched_load_id,
) -> None:
    case_id = _int_or_none(operations_case.get("id"))
    if case_id is None:
        return

    summary = _load_operations_case_email_summary(case_id)
    customer = (
        _safe_str(operations_case.get("customer", ""))
        or _safe_str(parsed.get("Customer", ""))
        or _case_customer_from_sender(record.get("source_sender", "") if hasattr(record, "get") else "")
    )
    booking = _safe_str(parsed.get("Booking Number", "")) or _safe_str(tokens.get("booking_number", "")) or "Pending"
    container = _safe_str(parsed.get("Container Number", "")) or _safe_str(tokens.get("container_number", "")) or "Pending"
    linked_load_id = _int_or_none(operations_case.get("linked_load_id")) or _int_or_none(matched_load_id)
    last_reply_by = _safe_str(summary.get("last_reply_by", ""))
    last_reply_mailbox = _safe_str(summary.get("last_reply_mailbox", ""))
    last_reply_time = _format_short_timestamp(summary.get("last_reply_at"))
    if last_reply_by and last_reply_time:
        reply_method = "TMS" if last_reply_mailbox.lower() == "tms" else "Yahoo Mail"
        last_reply = f"{_case_customer_from_sender(last_reply_by)} - {last_reply_time} ({reply_method})"
    else:
        last_reply = "-"

    st.markdown("### Operations Case Workspace")
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.metric("Case #", _safe_str(operations_case.get("case_number", "")) or f"Case {case_id}")
    r1c2.metric("Customer", customer or "-")
    r1c3.metric("Booking", booking)
    r1c4.metric("Container", container)

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.metric("Load", linked_load_id or "Not Created")
    r2c2.metric("Priority", _safe_str(operations_case.get("priority", "")) or "-")
    r2c3.metric("Owner", _safe_str(operations_case.get("owner", "")) or "Unassigned")
    r2c4.metric("Status", _safe_str(operations_case.get("status", "")) or "-")

    r3c1, r3c2, r3c3, r3c4 = st.columns(4)
    r3c1.metric("Last Reply", last_reply)
    r3c2.metric("Last Customer Email", _format_relative_timestamp(summary.get("last_customer_email_at")))
    r3c3.metric("Total Messages", int(summary.get("total_messages", 0) or operations_case.get("message_count", 0) or 0))
    r3c4.metric("SLA", _format_case_sla_label(operations_case))


def _render_operations_case_panel(
    *,
    selected_id: int,
    operations_case: dict,
    matched_load_id,
    show_timeline: bool = True,
) -> None:
    case_id = _int_or_none(operations_case.get("id"))
    if case_id is None:
        st.warning("No Operations Case is linked yet. Save classification or refresh this request to create one.")
        return

    case_number = _safe_str(operations_case.get("case_number", "")) or f"Case #{case_id}"
    case_status = _safe_str(operations_case.get("status", "New")) or "New"
    case_owner = _safe_str(operations_case.get("owner", "Unassigned")) or "Unassigned"
    case_priority = _safe_str(operations_case.get("priority", "Normal")) or "Normal"
    case_sla_status = _safe_str(operations_case.get("sla_status", "On Track")) or "On Track"
    linked_load_id = _int_or_none(operations_case.get("linked_load_id")) or _int_or_none(matched_load_id)

    with st.expander(f"Operations Case - {case_number}", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Case", case_number)
        c2.metric("Status", case_status)
        c3.metric("Owner", case_owner)
        c4.metric("Priority", case_priority)
        c5.metric("Linked Load", linked_load_id or "-")
        c6.metric("SLA", case_sla_status)
        due1, due2 = st.columns(2)
        due1.caption(f"First response due: {_safe_str(operations_case.get('first_response_due_at', '')) or '-'}")
        due2.caption(f"Resolution due: {_safe_str(operations_case.get('resolution_due_at', '')) or '-'}")

        with st.form(f"operations_case_update_{case_id}_{selected_id}"):
            f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
            status_options = list(OPERATIONS_CASE_STATUSES)
            if case_status not in status_options:
                status_options.insert(0, case_status)
            owner_options = list(OPERATIONS_CASE_OWNERS)
            case_owner_is_known = case_owner in owner_options
            if case_owner not in owner_options:
                owner_options.insert(0, case_owner)
            priority_options = list(OPERATIONS_CASE_PRIORITIES)
            if case_priority not in priority_options:
                priority_options.insert(0, case_priority)

            new_status = f1.selectbox(
                "Case Status",
                status_options,
                index=status_options.index(case_status),
                key=f"case_status_{case_id}_{selected_id}",
            )
            new_owner = f2.selectbox(
                "Owner",
                owner_options,
                index=owner_options.index(case_owner),
                key=f"case_owner_{case_id}_{selected_id}",
            )
            new_priority = f3.selectbox(
                "Priority",
                priority_options,
                index=priority_options.index(case_priority),
                key=f"case_priority_{case_id}_{selected_id}",
            )
            new_linked_load_id = f4.number_input(
                "Linked Load ID",
                min_value=0,
                value=int(linked_load_id or 0),
                step=1,
                key=f"case_linked_load_{case_id}_{selected_id}",
            )
            custom_owner = st.text_input(
                "Custom Owner",
                value="" if case_owner_is_known else case_owner,
                placeholder="Optional dispatcher or manager name",
                key=f"case_custom_owner_{case_id}_{selected_id}",
            )
            next_action = st.text_area(
                "Next Action",
                value=_safe_str(operations_case.get("next_action", "")),
                height=80,
                key=f"case_next_action_{case_id}_{selected_id}",
            )
            if st.form_submit_button("Save Case"):
                final_owner = _safe_str(custom_owner) or new_owner
                _update_operations_case(
                    case_id=case_id,
                    status=new_status,
                    owner=final_owner,
                    priority=new_priority,
                    linked_load_id=new_linked_load_id or None,
                    next_action=next_action,
                )
                refresh_data()
                st.success("Operations Case updated.")
                st.rerun()

        q1, q2, q3, q4 = st.columns(4)
        with q1:
            if st.button("Waiting Customer", key=f"case_waiting_customer_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Customer", "Waiting on customer response.")
                refresh_data()
                st.rerun()
        with q2:
            if st.button("Waiting Dispatcher", key=f"case_waiting_dispatcher_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Dispatcher", "Dispatcher needs to review and respond.")
                refresh_data()
                st.rerun()
        with q3:
            if st.button("Close Case", key=f"case_close_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Closed", "Case closed by operations.")
                execute(
                    """
                    update order_intake
                    set review_status = 'Closed'
                    where case_id = :case_id
                       or id = :intake_id
                    """,
                    {"case_id": case_id, "intake_id": int(selected_id)},
                )
                refresh_data()
                st.rerun()
        with q4:
            if st.button("Reopen Case", key=f"case_reopen_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Reopened", "Case reopened by operations.")
                execute(
                    """
                    update order_intake
                    set review_status = 'Open'
                    where case_id = :case_id
                       or id = :intake_id
                    """,
                    {"case_id": case_id, "intake_id": int(selected_id)},
                )
                refresh_data()
                st.rerun()

        w1, w2, w3, w4 = st.columns(4)
        with w1:
            if st.button("Waiting Manager", key=f"case_waiting_manager_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Manager", "Waiting on manager review.")
                refresh_data()
                st.rerun()
        with w2:
            if st.button("Waiting Driver", key=f"case_waiting_driver_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Driver", "Waiting on driver update.")
                refresh_data()
                st.rerun()
        with w3:
            if st.button("Waiting Port", key=f"case_waiting_port_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Port", "Waiting on port or terminal response.")
                refresh_data()
                st.rerun()
        with w4:
            if st.button("Waiting Warehouse", key=f"case_waiting_warehouse_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Warehouse", "Waiting on warehouse response.")
                refresh_data()
                st.rerun()

        note_body = st.text_area(
            "Internal Note",
            value="",
            height=90,
            key=f"case_note_{case_id}_{selected_id}",
            placeholder="Internal notes stay inside Operations and do not go to the customer.",
        )
        if st.button("Add Internal Note", key=f"case_add_note_{case_id}_{selected_id}", use_container_width=True):
            if not note_body.strip():
                st.error("Internal note is blank.")
            else:
                _add_operations_case_note(case_id, note_body.strip())
                refresh_data()
                st.success("Internal note added.")
                st.rerun()

        if show_timeline:
            timeline_df = _load_operations_case_timeline(case_id)
            if timeline_df.empty:
                st.info("Case timeline will appear after emails, notes, replies, or load actions are linked.")
            else:
                timeline_display = timeline_df.copy()
                timeline_display["event_time"] = pd.to_datetime(
                    timeline_display["event_at"],
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                timeline_display = timeline_display[
                    ["event_time", "event_type", "actor", "title", "details"]
                ].rename(
                    columns={
                        "event_time": "Time",
                        "event_type": "Type",
                        "actor": "Actor",
                        "title": "Title",
                        "details": "Details",
                    }
                )
                st.dataframe(timeline_display, use_container_width=True, hide_index=True)

        owner_history_df = _load_operations_case_owner_history(case_id)
        if not owner_history_df.empty:
            with st.expander("Ownership History", expanded=False):
                history_display = owner_history_df.copy()
                history_display["changed_at"] = pd.to_datetime(
                    history_display["changed_at"],
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                st.dataframe(history_display, use_container_width=True, hide_index=True)

        recent_cases = _load_recent_operations_cases(case_id)
        if not recent_cases.empty:
            st.caption("Duplicate Case Merge")
            case_options = [None] + recent_cases.to_dict("records")

            def _case_merge_label(option) -> str:
                if option is None:
                    return "Select target case"
                return (
                    f"{option.get('case_number')} | {option.get('status')} | "
                    f"{option.get('customer') or '-'} | {option.get('source_subject') or '-'}"
                )

            target_case = st.selectbox(
                "Merge this case into",
                case_options,
                format_func=_case_merge_label,
                key=f"case_merge_target_{case_id}_{selected_id}",
            )
            if st.button(
                "Merge Duplicate Case",
                key=f"case_merge_{case_id}_{selected_id}",
                use_container_width=True,
                disabled=target_case is None,
            ):
                if _merge_operations_cases(case_id, target_case.get("id")):
                    refresh_data()
                    st.success(f"Merged {case_number} into {target_case.get('case_number')}.")
                    st.rerun()
                else:
                    st.error("Could not merge the selected cases.")
