from __future__ import annotations

import streamlit as st

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.exceptions import AuthorizationError
from application.inbox.commands import request_inbox_sync
from db_client import read_df
from services import operations_inbox_service as ops
from ui_components.operations_inbox_state import close_work_item


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


def _render_manual_inbox_processing(principal: AuthenticatedActor) -> None:
    """Sync Email Engine / Refresh Inbox / Recheck Next Batch - relocated
    here from the normal Operations Inbox dispatcher view. Routine
    processing already runs automatically (see
    .github/workflows/process-jobs.yml -> scripts/process_worker_jobs.py
    -> workers/inbox_handlers.py, every 5 minutes); these are
    troubleshooting-only controls, not part of daily dispatcher workflow.
    Admin/Diagnostics is already gated to Role.MANAGER/Role.ADMIN at the
    section/router level (application/auth/permissions.py's
    ROLE_ALLOWED_SECTIONS) - Recheck Next Batch additionally enforces
    Permission.WORK_ITEM_MANAGE explicitly here because it calls a raw
    service function with no application-command layer of its own, so
    page visibility alone would otherwise be its only gate."""
    with st.expander("Manual Inbox Processing (Advanced / Debug)", expanded=False):
        st.caption(
            "Routine processing already runs automatically via the scheduled worker. "
            "These controls are for troubleshooting only - normal dispatcher work does "
            "not need them."
        )
        sync_col, refresh_col, recheck_col = st.columns(3)

        with sync_col:
            if st.button("Sync Email Engine", key="admin_operations_sync_email_engine", width="stretch"):
                try:
                    sync_result = request_inbox_sync(actor=principal)
                except AuthorizationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["operations_email_import_result"] = {
                        "queued": True,
                        "job_id": sync_result.job_id,
                    }
                    st.success(
                        f"Inbox sync queued (job #{sync_result.job_id}). "
                        "New messages will appear once the worker processes it."
                    )
                    close_work_item(st.session_state)
                    ops.refresh_data()
                    st.rerun()

        with refresh_col:
            if st.button("Refresh Inbox", key="admin_operations_refresh_inbox", width="stretch"):
                close_work_item(st.session_state)
                ops.refresh_data()
                st.rerun()

        with recheck_col:
            if st.button("Recheck Next Batch", key="admin_operations_recheck_smart_groups", width="stretch"):
                try:
                    require_permission(principal, Permission.WORK_ITEM_MANAGE)
                except AuthorizationError as exc:
                    st.error(str(exc))
                else:
                    with st.spinner("Running fast triage on the next small batch..."):
                        triage_result = ops.auto_classify_open_inbox_items(limit=25, time_budget_seconds=8)
                    st.session_state["operations_smart_group_update_result"] = triage_result
                    close_work_item(st.session_state)
                    st.rerun()


def _render_email_synchronization(principal: AuthenticatedActor) -> None:
    st.markdown("### Email Synchronization")
    st.caption("Mailbox connectivity, synchronization settings, results, and technical status.")

    _render_manual_inbox_processing(principal)

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
    st.markdown("### Document Parsing & Attachment Diagnostics")
    st.caption(
        "Admin recovery tools for attachment discovery, manual upload, parsing, "
        "comparison, and reconciliation."
    )
    ops.render_operations_pdf_panel(
        selected_id=intake_id,
        record=record,
        parsed=parsed,
        subject=str(record.get("source_subject") or ""),
        sender=str(record.get("source_sender") or ""),
        body=str(record.get("raw_text") or ""),
        matched_load_id=record.get("matched_load_id"),
        conversation_key=str(record.get("conversation_key") or ""),
    )
    with st.expander("Latest Work-Item Opening Timing", expanded=False):
        timing = st.session_state.get("operations_open_timing_last")
        if timing:
            st.json(timing)
        else:
            st.caption(
                "Open a dispatcher work item in this session to capture a timing breakdown."
            )

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


def render_email_imports(principal: AuthenticatedActor) -> None:
    """Render email import history from the email_imports table."""
    st.subheader("Email Imports / Diagnostics")
    st.caption("Admin view for mailbox troubleshooting, skipped messages, raw import metadata, and parser issues. Dispatchers should work daily email from Operations Inbox.")
    _render_email_synchronization(principal)
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
