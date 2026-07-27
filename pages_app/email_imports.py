from __future__ import annotations

import streamlit as st

from db_client import read_df
from services import operations_inbox_service as ops


def _render_imap_result() -> None:
    result = st.session_state.get("operations_imap_diagnostic_result") or []
    if not result:
        st.caption("No IMAP connection test has been run in this session.")
        return

    for account in result:
        email = account.get("email", "-")
        if account.get("login_success"):
            st.success(
                f"{email}: login OK; folder "
                f"'{account.get('selected_folder', '-')}' "
                f"with {account.get('messages_found', 0)} message(s)."
            )
        elif not account.get("credentials_configured"):
            st.warning(f"{email}: no app password configured.")
        else:
            st.error(
                f"{email}: {account.get('error_type', 'Error')}: "
                f"{account.get('error_message', 'login failed')}"
            )


def _render_email_synchronization() -> None:
    st.markdown("### Email Synchronization")
    st.caption("Mailbox connectivity, synchronization settings, results, and technical status.")

    if st.button(
        "Test IMAP Connections",
        key="admin_test_operations_imap_connections",
        icon=":material/cable:",
    ):
        with st.spinner("Testing configured operations mailboxes..."):
            st.session_state["operations_imap_diagnostic_result"] = (
                ops.diagnose_operations_email_accounts()
            )
    _render_imap_result()

    with st.expander("Latest Email Sync Result", expanded=False):
        result = st.session_state.get("operations_email_import_result")
        if result:
            st.json(result)
        else:
            st.caption("No email synchronization result is available in this session.")

    with st.expander("Email Sync Settings", expanded=False):
        st.number_input(
            "Recent email sync limit",
            min_value=12,
            max_value=300,
            value=int(st.session_state.get("operations_email_sync_limit", 75)),
            step=10,
            key="operations_email_sync_limit",
        )

    with st.expander("Email Sync KPIs", expanded=False):
        try:
            sync_metrics = ops._operations_email_sync_metrics()
            case_metrics = ops._operations_case_metrics()
        except Exception as exc:
            st.warning(f"Could not load synchronization KPIs: {exc}")
        else:
            metric_columns = st.columns(4)
            metric_columns[0].metric("Synced Inbox", int(sync_metrics.get("inbound", 0)))
            metric_columns[1].metric("Synced Sent", int(sync_metrics.get("outbound", 0)))
            metric_columns[2].metric("Email Threads", int(sync_metrics.get("threads", 0)))
            metric_columns[3].metric("Last Sync", sync_metrics.get("last_sync") or "-")
            case_columns = st.columns(4)
            case_columns[0].metric("Open Cases", int(case_metrics.get("open", 0)))
            case_columns[1].metric("Waiting Dispatch", int(case_metrics.get("waiting_dispatch", 0)))
            case_columns[2].metric("Waiting Customer", int(case_metrics.get("waiting_customer", 0)))
            case_columns[3].metric("Closed Cases", int(case_metrics.get("closed", 0)))

    with st.expander("Email Synchronization Metadata", expanded=False):
        result = st.session_state.get("operations_email_import_result") or {}
        diagnostics = result.get("diagnostics") or {}
        st.json(diagnostics or {"status": "No synchronization metadata available."})


def _render_operations_diagnostics() -> None:
    st.markdown("### Thread Diagnostics")
    intake_id = int(
        st.number_input(
            "Operations work-item ID",
            min_value=0,
            value=0,
            step=1,
            key="admin_operations_diagnostic_intake_id",
        )
    )
    if intake_id <= 0:
        st.caption("Enter a work-item ID to inspect thread and parser diagnostics.")
        return

    record_df = ops._load_operations_inbox_record(intake_id)
    if record_df is None or record_df.empty:
        st.warning("The selected work item was not found.")
        return

    record = record_df.iloc[0].to_dict()
    parsed = ops._coerce_json_dict(record.get("parsed_data"))
    with st.expander("Thread Sync Debug", expanded=False):
        st.write(f"**Message ID:** {record.get('source_message_id') or '-'}")
        st.write(f"**Thread ID:** {record.get('email_thread_id') or '-'}")
        st.write(f"**Conversation Key:** {record.get('conversation_key') or '-'}")
        st.write(f"**In Reply To:** {record.get('email_in_reply_to') or '-'}")
        if st.button(
            "Sync Recent Mail Then Refresh This Thread",
            key=f"admin_sync_thread_{intake_id}",
        ):
            st.session_state["operations_email_import_result"] = (
                ops.sync_operations_email_engine(
                    limit=int(st.session_state.get("operations_email_sync_limit", 75))
                )
            )
            ops.refresh_data()
            st.rerun()

    st.markdown("### Operations Diagnostics")
    with st.expander("Control Center Snapshot", expanded=False):
        st.json(
            {
                "request_type": record.get("request_type"),
                "review_status": record.get("review_status"),
                "action_required": record.get("action_required"),
                "confidence_score": record.get("confidence_score"),
                "matched_load_id": record.get("matched_load_id"),
            }
        )
    with st.expander("Extracted Fields", expanded=False):
        st.json({key: value for key, value in parsed.items() if not str(key).startswith("_")})
    with st.expander("Advanced Triage / AI Details", expanded=False):
        st.json(
            {
                "fast_triage": parsed.get("_fast_triage") or {},
                "parser_failures": parsed.get("_parser_failures") or [],
                "candidate_diagnostics": parsed.get("_field_candidates") or {},
            }
        )


def _render_maintenance_cleanup() -> None:
    st.markdown("### Maintenance / Cleanup")
    with st.expander("Bulk Cleanup", expanded=False):
        st.warning("Cleanup changes inbox records. Preview and confirm before applying.")
        cleanup_limit = st.number_input(
            "Cleanup scan limit",
            min_value=10,
            max_value=500,
            value=100,
            step=10,
            key="admin_operations_cleanup_limit",
        )
        if st.button("Preview Non-Dispatch Cleanup", key="admin_preview_non_dispatch_cleanup"):
            from pages_app.operations_inbox import _bulk_archive_obvious_non_dispatch_emails

            st.session_state["operations_bulk_cleanup_preview"] = (
                _bulk_archive_obvious_non_dispatch_emails(
                    dry_run=True,
                    limit=int(cleanup_limit),
                )
            )
        preview = st.session_state.get("operations_bulk_cleanup_preview") or {}
        if preview:
            st.write(
                f"Matched: {preview.get('matched', 0)} | "
                f"Updated: {preview.get('updated', 0)}"
            )
            preview_df = preview.get("preview")
            if preview_df is not None and not preview_df.empty:
                st.dataframe(preview_df, hide_index=True)

        cleanup_confirmed = st.checkbox(
            "I reviewed the preview and confirm this cleanup",
            key="admin_confirm_non_dispatch_cleanup",
        )
        if st.button(
            "Apply Non-Dispatch Cleanup",
            key="admin_apply_non_dispatch_cleanup",
            disabled=not cleanup_confirmed,
        ):
            from pages_app.operations_inbox import _bulk_archive_obvious_non_dispatch_emails

            result = _bulk_archive_obvious_non_dispatch_emails(
                dry_run=False,
                limit=int(cleanup_limit),
            )
            st.success(f"Archived {result.get('updated', 0)} item(s).")
            ops.refresh_data()
            st.rerun()


def render_email_imports() -> None:
    """Render email import history from the email_imports table."""
    st.subheader("Email Imports / Diagnostics")
    st.caption("Admin view for mailbox troubleshooting, skipped messages, raw import metadata, and parser issues. Dispatchers should work daily email from Operations Inbox.")
    _render_email_synchronization()
    _render_operations_diagnostics()
    _render_maintenance_cleanup()

    st.markdown("### Import History")
    try:
        imports = read_df(
            """
            select
                gmail_message_id,
                subject,
                sender,
                received_at,
                pdf_filename,
                parsed_status,
                created_load_id,
                created_at
            from email_imports
            order by created_at desc
            """
        )

        st.dataframe(
            imports,
            hide_index=True,
        )

    except Exception as exc:
        st.error(f"Could not load email imports: {exc}")
