from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from database.db_client import DispatchDatabaseClient, execute, read_df
from services.workflow_constants import normalize_service_flow

INTAKE_STATUSES = [
    "Needs Review",
    "Ready to Create Load",
    "Created Load",
    "Duplicate",
    "Rejected",
    "Needs Customer Info",
    "Needs Appointment",
]


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
    if not (str(parsed_data.get("Booking Number", "") or "").strip() or str(parsed_data.get("Reference Number", "") or "").strip()):
        missing.append("Booking or Reference Number")
    if not str(parsed_data.get("Customer", "") or "").strip():
        missing.append("Customer")
    if not (str(parsed_data.get("Warehouse", "") or "").strip() or str(parsed_data.get("Address", "") or "").strip()):
        missing.append("Warehouse or Address")

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


def create_load_from_intake(record_id: int, edited_data: dict[str, Any], *, conn: Connection | None = None) -> int:
    """Create a load from a reviewed intake record, plus its document row
    and intake-status update. Idempotent - see
    create_load_from_intake_with_status for the full contract. Callers
    that only need the resulting load id (new or pre-existing) can use
    this unchanged wrapper; callers that need to know whether a new load
    was actually inserted should call create_load_from_intake_with_status
    directly."""
    load_id, _created = create_load_from_intake_with_status(record_id, edited_data, conn=conn)
    return load_id


def create_load_from_intake_with_status(
    record_id: int, edited_data: dict[str, Any], *, conn: Connection | None = None
) -> tuple[int, bool]:
    """Create a load from a reviewed intake record, plus its document row
    and intake-status update. Returns (load_id, created).

    Idempotent: passes record_id as add_row's source_intake_id, so a
    second call for the same record_id does not insert a second loads
    row - it resolves to the load created by the first call (see
    DispatchDatabaseClient.add_row and
    database/loads_source_intake_idempotency_migration.sql). `created` is
    False when an existing row was resolved this way rather than a new
    one being inserted; callers that also write a document row and
    advance intake status (this function) or downstream records (e.g.
    services.operations_inbox_service.create_load_from_inbox_item) should
    skip those additional writes when `created` is False, since they were
    already applied by the original, already-committed call - repeating
    them would duplicate document/communication rows instead of no-oping
    like the plain UPDATE statements here do.

    `conn` is optional and defaults to None, so every existing caller
    keeps its current one-call-per-statement behavior unchanged. Pass an
    open connection (see db_client.transaction()) to run all writes as
    part of a larger multi-statement business command instead - they
    then commit or roll back together with the rest of that command."""
    booking_value = edited_data.get("Booking Number", "") or edited_data.get("Reference Number", "")
    client = DispatchDatabaseClient()
    created = client.add_row(
        {
            "TYPE": normalize_service_flow(edited_data.get("TYPE", "Import"), default="Import"),
            "Booking Number": booking_value,
            "Reference Number": edited_data.get("Reference Number", ""),
            "Customer": edited_data.get("Customer", ""),
            "Container Number": edited_data.get("Container Number", ""),
            "Port": edited_data.get("Port", ""),
            "Warehouse": edited_data.get("Warehouse", ""),
            "Address": edited_data.get("Address", ""),
            "Full Return Terminal": edited_data.get("Full Return Terminal", ""),
            "Document Cutoff": edited_data.get("Document Cutoff", ""),
            "Delivery Need Date": edited_data.get("Delivery Need Date", ""),
            "LFD": edited_data.get("LFD", ""),
            "Size": edited_data.get("Size", ""),
            "Status": edited_data.get("Status", "New") or "New",
            "Dispatcher Notes": edited_data.get("Dispatcher Notes", ""),
        },
        source_intake_id=record_id,
        conn=conn,
    )

    if not created.created:
        return int(created.id), False

    record = get_intake_record(record_id)
    file_path = record.get("file_path")
    filename = record.get("filename")

    document_sql = text(
        """
        insert into documents (load_id, document_type, filename, file_path, source)
        values (:load_id, :document_type, :filename, :file_path, :source)
        """
    )
    document_params = {
        "load_id": created.id,
        "document_type": "load_order",
        "filename": filename,
        "file_path": file_path,
        "source": "order_intake",
    }
    intake_status_sql = text(
        """
        update order_intake
        set intake_status = 'Created Load',
            linked_load_id = :load_id,
            reviewed_at = now(),
            reviewed_by = 'streamlit'
        where id = :id
        """
    )
    intake_status_params = {"load_id": created.id, "id": record_id}

    if conn is not None:
        if file_path and filename and Path(file_path).exists():
            conn.execute(document_sql, document_params)
        conn.execute(intake_status_sql, intake_status_params)
        return int(created.id), True

    if file_path and filename and Path(file_path).exists():
        execute(str(document_sql), document_params)

    execute(str(intake_status_sql), intake_status_params)

    return int(created.id), True
