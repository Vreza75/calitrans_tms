from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from config import DOCUMENT_STORAGE_DIR
from db_client import DispatchDatabaseClient, execute, read_df
from email_client import fetch_recent_load_emails
from email_parser import parse_email_text
from order_parser import extract_text_from_pdf, parse_order_text


INTAKE_STATUSES = [
    "Needs Review",
    "Ready to Create Load",
    "Created Load",
    "Duplicate",
    "Rejected",
    "Needs Customer Info",
    "Needs Appointment",
]


def _save_uploaded_file(uploaded_file, prefix: str = "intake") -> str:
    storage_dir = Path(DOCUMENT_STORAGE_DIR) / "order_intake"
    storage_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(uploaded_file.name).name
    output_path = storage_dir / f"{prefix}_{safe_name}"

    uploaded_file.seek(0)
    output_path.write_bytes(uploaded_file.read())
    uploaded_file.seek(0)

    return str(output_path)


def _json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, default=str)


def create_intake_record(
    *,
    source: str,
    filename: str | None,
    file_path: str | None,
    parsed_data: dict[str, Any],
    raw_text: str,
    source_subject: str | None = None,
    source_sender: str | None = None,
    action_required: str | None = None,
) -> None:
    execute(
        """
        insert into order_intake (
            source,
            source_subject,
            source_sender,
            filename,
            file_path,
            parsed_data,
            raw_text,
            intake_status,
            action_required
        )
        values (
            :source,
            :source_subject,
            :source_sender,
            :filename,
            :file_path,
            cast(:parsed_data as jsonb),
            :raw_text,
            :intake_status,
            :action_required
        )
        """,
        {
            "source": source,
            "source_subject": source_subject,
            "source_sender": source_sender,
            "filename": filename,
            "file_path": file_path,
            "parsed_data": _json_dump(parsed_data),
            "raw_text": raw_text,
            "intake_status": "Needs Review",
            "action_required": action_required or _suggest_action(parsed_data),
        },
    )


def _suggest_action(parsed_data: dict[str, Any]) -> str:
    missing = []
    for field in ["Booking Number", "Customer", "Warehouse"]:
        if not str(parsed_data.get(field, "") or "").strip():
            missing.append(field)

    if missing:
        return "Missing: " + ", ".join(missing)

    return "Review parsed details and create load"


def get_intake_queue(status_filter: str = "Open") -> pd.DataFrame:
    if status_filter == "Open":
        where = "where intake_status not in ('Created Load', 'Rejected', 'Duplicate')"
        params = {}
    elif status_filter == "All":
        where = ""
        params = {}
    else:
        where = "where intake_status = :status"
        params = {"status": status_filter}

    return read_df(
        f"""
        select
            id,
            source,
            source_subject,
            source_sender,
            filename,
            intake_status,
            action_required,
            linked_load_id,
            created_at,
            reviewed_at
        from order_intake
        {where}
        order by created_at desc
        """,
        params,
    )


def get_intake_record(record_id: int) -> dict[str, Any]:
    df = read_df("select * from order_intake where id = :id", {"id": record_id})
    if df.empty:
        raise ValueError("Order intake record not found.")
    record = df.iloc[0].to_dict()

    parsed = record.get("parsed_data") or {}
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            parsed = {}
    record["parsed_data"] = parsed
    return record


def update_intake_status(record_id: int, status: str, action_required: str | None = None) -> None:
    execute(
        """
        update order_intake
        set intake_status = :status,
            action_required = coalesce(:action_required, action_required),
            reviewed_at = now(),
            reviewed_by = 'streamlit'
        where id = :id
        """,
        {"id": record_id, "status": status, "action_required": action_required, "id": record_id},
    )


def create_load_from_intake(record_id: int, edited_data: dict[str, Any]) -> int:
    client = DispatchDatabaseClient()
    created = client.add_row(
        {
            "TYPE": edited_data.get("TYPE", "OTR Import"),
            "Booking Number": edited_data.get("Booking Number", ""),
            "Reference Number": edited_data.get("Reference Number", ""),
            "Customer": edited_data.get("Customer", ""),
            "Container Number": edited_data.get("Container Number", ""),
            "Port": edited_data.get("Port", ""),
            "Warehouse": edited_data.get("Warehouse", ""),
            "Document Cutoff": edited_data.get("Document Cutoff", ""),
            "Delivery Need Date": edited_data.get("Delivery Need Date", ""),
            "LFD": edited_data.get("LFD", ""),
            "Status": edited_data.get("Status", "New") or "New",
            "Dispatcher Notes": edited_data.get("Dispatcher Notes", ""),
        }
    )

    record = get_intake_record(record_id)
    file_path = record.get("file_path")
    filename = record.get("filename")

    if file_path and filename and Path(file_path).exists():
        execute(
            """
            insert into documents (load_id, document_type, filename, file_path, source)
            values (:load_id, :document_type, :filename, :file_path, :source)
            """,
            {
                "load_id": created.id,
                "document_type": "load_order",
                "filename": filename,
                "file_path": file_path,
                "source": "order_intake",
            },
        )

    execute(
        """
        update order_intake
        set intake_status = 'Created Load',
            linked_load_id = :load_id,
            reviewed_at = now(),
            reviewed_by = 'streamlit'
        where id = :id
        """,
        {"load_id": created.id, "id": record_id},
    )

    return int(created.id)


def render_order_upload_panel() -> None:
    st.markdown("### Upload Order PDF")

    uploaded_file = st.file_uploader(
        "Upload order PDF",
        type=["pdf"],
        key="order_intake_pdf_upload",
    )

    if uploaded_file is not None:
        try:
            raw_text = extract_text_from_pdf(uploaded_file)
            parsed = parse_order_text(raw_text)
        except Exception as exc:
            st.error(f"Could not parse PDF: {exc}")
            return

        st.markdown("#### Parsed Preview")
        st.json(parsed)

        action_required = _suggest_action(parsed)
        st.info(action_required)

        if st.button("Add to Action Queue", key="add_pdf_to_intake"):
            file_path = _save_uploaded_file(uploaded_file, prefix="pdf")
            create_intake_record(
                source="manual_pdf_upload",
                filename=uploaded_file.name,
                file_path=file_path,
                parsed_data=parsed,
                raw_text=raw_text,
                action_required=action_required,
            )
            st.success("Order added to Action Queue.")
            st.cache_data.clear()
            st.rerun()


def render_email_intake_panel() -> None:
    st.markdown("### Recent Email Orders")
    st.caption("Pull recent load emails. Add body-only, PDF-only, or combined email/PDF review to the action queue.")

    if st.button("Check Recent Load Emails", key="check_recent_load_emails"):
        try:
            st.session_state["recent_load_emails"] = fetch_recent_load_emails(limit=10)
        except Exception as exc:
            st.error(f"Could not fetch recent emails: {exc}")

    emails = st.session_state.get("recent_load_emails", [])

    if not emails:
        st.info("No recent emails loaded yet.")
        return

    for idx, item in enumerate(emails):
        subject = item.get("subject", "")
        sender = item.get("from", "")
        body = item.get("body", "") or item.get("snippet", "") or ""
        attachments = item.get("attachments", [])

        with st.expander(f"{subject} — {sender}", expanded=False):
            st.markdown("#### Email Verification Window")
            st.write(f"**Subject:** {subject}")
            st.write(f"**From:** {sender}")
            st.text_area("Email Body", value=body, height=220, key=f"email_body_preview_{idx}")

            try:
                body_parsed = parse_email_text(subject, body)
            except Exception as exc:
                st.warning(f"Could not parse email body: {exc}")
                body_parsed = {}

            pdfs = [a for a in attachments if a.get("filename", "").lower().endswith(".pdf")]
            pdf_options = ["No PDF / body only"] + [p["filename"] for p in pdfs]
            selected_pdf_name = st.selectbox("PDF attachment", pdf_options, key=f"email_pdf_select_{idx}")

            pdf_parsed = {}
            pdf_text = ""
            selected_pdf = None

            if selected_pdf_name != "No PDF / body only":
                selected_pdf = next(p for p in pdfs if p["filename"] == selected_pdf_name)
                from io import BytesIO
                pdf_file = BytesIO(selected_pdf["content"])
                pdf_file.name = selected_pdf["filename"]

                try:
                    pdf_text = extract_text_from_pdf(pdf_file)
                    pdf_parsed = parse_order_text(pdf_text)
                except Exception as exc:
                    st.warning(f"Could not parse selected PDF: {exc}")
                    pdf_parsed = {}

            all_fields = [
                "TYPE", "Customer", "Booking Number", "Reference Number", "Container Number",
                "Port", "Warehouse", "Delivery Need Date", "Document Cutoff", "LFD", "Dispatcher Notes",
            ]

            final_data = {}
            rows = []

            for field in all_fields:
                body_value = str(body_parsed.get(field, "") or "").strip()
                pdf_value = str(pdf_parsed.get(field, "") or "").strip()
                final_value = pdf_value or body_value
                final_data[field] = final_value

                if body_value and pdf_value and body_value != pdf_value:
                    status = "Review mismatch"
                elif final_value:
                    status = "Found"
                else:
                    status = "Blank"

                rows.append({
                    "Field": field,
                    "Email Body": body_value,
                    "PDF": pdf_value,
                    "Final Value": final_value,
                    "Status": status,
                })

            st.markdown("#### Accuracy Check")
            c1, c2, c3 = st.columns(3)
            c1.metric("Estimated Load Count", 1)
            c2.metric("Email Body Fields Found", sum(1 for v in body_parsed.values() if str(v).strip()))
            c3.metric("PDF Fields Found", sum(1 for v in pdf_parsed.values() if str(v).strip()))
            st.dataframe(rows, use_container_width=True, hide_index=True)

            missing = [f for f in ["Booking Number", "Customer", "Warehouse"] if not str(final_data.get(f, "") or "").strip()]
            action_required = "Missing: " + ", ".join(missing) if missing else "Review parsed details and create load"
            st.info(f"Suggested action: {action_required}")

            col1, col2, col3 = st.columns(3)

            if col1.button("Add Body Only to Queue", key=f"add_body_only_{idx}"):
                create_intake_record(
                    source="email_body",
                    source_subject=subject,
                    source_sender=sender,
                    filename=None,
                    file_path=None,
                    parsed_data=body_parsed,
                    raw_text=body,
                    action_required=action_required,
                )
                st.success("Email body added to Action Queue.")
                st.cache_data.clear()
                st.rerun()

            if col2.button("Add PDF Only to Queue", key=f"add_pdf_only_{idx}", disabled=selected_pdf is None):
                if selected_pdf is not None:
                    from io import BytesIO
                    pdf_file = BytesIO(selected_pdf["content"])
                    pdf_file.name = selected_pdf["filename"]
                    file_path = _save_uploaded_file(pdf_file, prefix="email_pdf")
                    create_intake_record(
                        source="email_pdf",
                        source_subject=subject,
                        source_sender=sender,
                        filename=selected_pdf["filename"],
                        file_path=file_path,
                        parsed_data=pdf_parsed,
                        raw_text=pdf_text,
                        action_required=action_required,
                    )
                    st.success("Email PDF added to Action Queue.")
                    st.cache_data.clear()
                    st.rerun()

            if col3.button("Add Combined Review to Queue", key=f"add_combined_{idx}"):
                file_path = None
                filename = None
                if selected_pdf is not None:
                    from io import BytesIO
                    pdf_file = BytesIO(selected_pdf["content"])
                    pdf_file.name = selected_pdf["filename"]
                    file_path = _save_uploaded_file(pdf_file, prefix="email_combined")
                    filename = selected_pdf["filename"]

                create_intake_record(
                    source="email_combined",
                    source_subject=subject,
                    source_sender=sender,
                    filename=filename,
                    file_path=file_path,
                    parsed_data=final_data,
                    raw_text=f"EMAIL BODY:\n{body}\n\nPDF TEXT:\n{pdf_text}",
                    action_required=action_required,
                )
                st.success("Combined email review added to Action Queue.")
                st.cache_data.clear()
                st.rerun()

def render_action_queue_panel() -> None:
    st.markdown("### Order Action Queue")

    c1, c2 = st.columns([2, 1])
    status_filter = c1.selectbox("Queue Filter", ["Open", "All"] + INTAKE_STATUSES)
    refresh = c2.button("Refresh Queue")
    if refresh:
        st.cache_data.clear()
        st.rerun()

    queue = get_intake_queue(status_filter)

    if queue.empty:
        st.success("No order intake items require action.")
        return

    st.dataframe(queue, use_container_width=True, hide_index=True)

    selected_id = st.selectbox(
        "Select order to review",
        queue["id"].astype(int).tolist(),
        format_func=lambda x: f"Intake #{x}",
    )

    record = get_intake_record(int(selected_id))
    parsed = record.get("parsed_data") or {}

    st.markdown("#### Review Parsed Order")

    with st.form(f"review_intake_{selected_id}"):
        col1, col2 = st.columns(2)

        with col1:
            type_val = st.selectbox(
                "TYPE",
                ["OTR Import", "OTR Export", "OTR Local Import"],
                index=0,
            )
            booking = st.text_input("Booking Number", value=str(parsed.get("Booking Number", "") or ""))
            reference = st.text_input("Reference Number", value=str(parsed.get("Reference Number", "") or ""))
            customer = st.text_input("Customer", value=str(parsed.get("Customer", "") or ""))
            container = st.text_input("Container Number", value=str(parsed.get("Container Number", "") or ""))

        with col2:
            port = st.text_input("Port", value=str(parsed.get("Port", "") or ""))
            warehouse = st.text_input("Warehouse", value=str(parsed.get("Warehouse", "") or ""))
            document_cutoff = st.text_input("Document Cutoff", value=str(parsed.get("Document Cutoff", "") or ""))
            delivery_need = st.text_input("Delivery Need Date", value=str(parsed.get("Delivery Need Date", "") or ""))
            lfd = st.text_input("LFD", value=str(parsed.get("LFD", "") or ""))

        status = st.selectbox("Initial Load Status", ["New", "Hold/Need Info", "Ready to Dispatch"])
        notes = st.text_area(
            "Dispatcher Notes",
            value=str(parsed.get("Dispatcher Notes", "") or record.get("action_required", "") or ""),
        )

        action = st.selectbox(
            "Queue Action",
            ["Create Load", "Mark Needs Customer Info", "Mark Needs Appointment", "Mark Duplicate", "Reject"],
        )

        submitted = st.form_submit_button("Submit Action")

    if submitted:
        if action == "Create Load":
            if not booking.strip():
                st.error("Booking Number is required before creating a load.")
            else:
                load_id = create_load_from_intake(
                    int(selected_id),
                    {
                        "TYPE": type_val,
                        "Booking Number": booking,
                        "Reference Number": reference,
                        "Customer": customer,
                        "Container Number": container,
                        "Port": port,
                        "Warehouse": warehouse,
                        "Document Cutoff": document_cutoff,
                        "Delivery Need Date": delivery_need,
                        "LFD": lfd,
                        "Status": status,
                        "Dispatcher Notes": notes,
                    },
                )
                st.success(f"Load created from intake item. Load ID: {load_id}")
                st.cache_data.clear()
                st.rerun()

        elif action == "Mark Needs Customer Info":
            update_intake_status(int(selected_id), "Needs Customer Info", notes)
            st.warning("Marked as needing customer info.")
            st.rerun()

        elif action == "Mark Needs Appointment":
            update_intake_status(int(selected_id), "Needs Appointment", notes)
            st.warning("Marked as needing appointment.")
            st.rerun()

        elif action == "Mark Duplicate":
            update_intake_status(int(selected_id), "Duplicate", notes)
            st.info("Marked as duplicate.")
            st.rerun()

        elif action == "Reject":
            update_intake_status(int(selected_id), "Rejected", notes)
            st.info("Rejected.")
            st.rerun()


def render_order_intake_workspace() -> None:
    st.subheader("Order Intake")
    st.caption("Upload PDFs, review recent email orders, and action new orders before they become active loads.")

    queue_df = get_intake_queue("Open")
    needs_review = int(queue_df["intake_status"].eq("Needs Review").sum()) if not queue_df.empty else 0
    needs_info = int(queue_df["intake_status"].isin(["Needs Customer Info", "Needs Appointment"]).sum()) if not queue_df.empty else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Open Intake Items", len(queue_df))
    c2.metric("Needs Review", needs_review)
    c3.metric("Needs Action", needs_info)

    tab1, tab2, tab3 = st.tabs(["Action Queue", "Upload PDF", "Email Orders"])

    with tab1:
        render_action_queue_panel()

    with tab2:
        render_order_upload_panel()

    with tab3:
        render_email_intake_panel()
