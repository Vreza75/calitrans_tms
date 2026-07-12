# services/operations_attachment_service.py

from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parseaddr
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ai_agents.hybrid_document_parser import parse_document_hybrid
from config import DOCUMENT_STORAGE_DIR
from db_client import execute, read_df
from services.load_matching_service import existing_load_columns
from services.order_parser import extract_text_from_pdf


OPERATIONS_ATTACHMENTS_KEY = "_operations_attachments"
OPERATIONS_PDF_ATTACHMENTS_KEY = "_operations_pdf_attachments"

OPERATIONS_ORDER_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Size",
    "Port",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "Document Cutoff",
    "LFD",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Contact Company",
    "Dispatcher Notes",
]


PARSED_TO_LOAD_COLUMN_MAP = {
    "TYPE": "type",
    "Customer": "customer",
    "Booking Number": "booking_number",
    "Reference Number": "reference_number",
    "Container Number": "container_number",
    "Size": "size",
    "Port": "port",
    "Warehouse": "warehouse",
    "Address": "address",
    "Delivery Need Date": "delivery_need_date",
    "Document Cutoff": "document_cutoff",
    "LFD": "lfd",
    "Contact Email": "contact_email",
    "Contact Phone": "contact_phone",
    "Dispatcher Notes": "dispatcher_notes",
}


# ============================================================
# Basic helpers
# ============================================================

def safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value_text = safe_str(value)
    if not value_text:
        return None

    try:
        return int(float(value_text))
    except Exception:
        return None


def coerce_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    return {}


def json_dump(data: dict) -> str:
    return json.dumps(data or {}, default=str)


def field_count(parsed: dict) -> int:
    return sum(1 for field in OPERATIONS_ORDER_FIELDS if safe_str(parsed.get(field, "")))


def safe_storage_name(value: str, fallback: str = "file") -> str:
    name = Path(str(value or fallback)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def operations_attachment_storage_dir() -> Path:
    storage_dir = Path(DOCUMENT_STORAGE_DIR) / "operations_inbox"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def is_pdf_filename(filename: str, content_type: str = "") -> bool:
    return (
        safe_str(filename).lower().endswith(".pdf")
        or safe_str(content_type).lower() == "application/pdf"
    )


# Compatibility aliases
_safe_str = safe_str
_int_or_none = int_or_none
_coerce_json_dict = coerce_json_dict
_json_dump = json_dump
_field_count = field_count
_safe_storage_name = safe_storage_name
_operations_pdf_storage_dir = operations_attachment_storage_dir
_is_pdf_filename = is_pdf_filename


# ============================================================
# Attachment text extraction
# ============================================================

def decode_text_attachment(content: bytes) -> str:
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            return (content or b"").decode(encoding, errors="ignore").strip()
        except Exception:
            continue
    return ""


def extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content or b"")) as archive:
            xml_data = archive.read("word/document.xml")
    except Exception:
        return ""

    try:
        root = ET.fromstring(xml_data)
    except Exception:
        return ""

    text_nodes = []

    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            text_nodes.append(node.text)
        elif node.tag.endswith("}p"):
            text_nodes.append("\n")

    return " ".join(text_nodes).replace(" \n ", "\n").strip()


def parse_operations_pdf_bytes(content: bytes, filename: str) -> tuple[str, dict]:
    pdf_file = BytesIO(content or b"")
    pdf_file.name = filename or "attachment.pdf"

    pdf_text = extract_text_from_pdf(pdf_file)

    if not pdf_text:
        return "", {}

    hybrid_doc_result = parse_document_hybrid(
        document_text=pdf_text,
        filename=filename,
        email_subject="",
        email_body="",
        company_memory={},
    )

    parsed = hybrid_doc_result.get("parsed_fields", {}) or {}
    parsed["_hybrid_document_parser"] = hybrid_doc_result

    return pdf_text, parsed


def parse_operations_attachment_bytes(
    content: bytes,
    filename: str,
    content_type: str = "",
) -> tuple[str, dict]:
    filename_lower = safe_str(filename).lower()
    content_type = safe_str(content_type).lower()

    if is_pdf_filename(filename, content_type):
        return parse_operations_pdf_bytes(content, filename)

    if filename_lower.endswith(".docx") or content_type.endswith("wordprocessingml.document"):
        text = extract_docx_text(content)

        if not text:
            return "", {}

        hybrid_doc_result = parse_document_hybrid(
            document_text=text,
            filename=filename,
            email_subject="",
            email_body="",
            company_memory={},
        )

        parsed = hybrid_doc_result.get("parsed_fields", {}) or {}
        parsed["_hybrid_document_parser"] = hybrid_doc_result

        return text, parsed

    if filename_lower.endswith((".txt", ".csv", ".tsv")) or content_type.startswith("text/"):
        text = decode_text_attachment(content)

        if not text:
            return "", {}

        hybrid_doc_result = parse_document_hybrid(
            document_text=text,
            filename=filename,
            email_subject="",
            email_body="",
            company_memory={},
        )

        parsed = hybrid_doc_result.get("parsed_fields", {}) or {}
        parsed["_hybrid_document_parser"] = hybrid_doc_result

        return text, parsed

    return "", {}


# Compatibility aliases
_decode_text_attachment = decode_text_attachment
_extract_docx_text = extract_docx_text
_parse_operations_pdf_bytes = parse_operations_pdf_bytes
_parse_operations_attachment_bytes = parse_operations_attachment_bytes


# ============================================================
# Save / read / parse saved attachments
# ============================================================

def save_operations_attachment(
    *,
    content: bytes,
    filename: str,
    message_id: str,
    attachment_index: int,
    content_type: str = "",
) -> dict:
    safe_message = safe_storage_name(message_id, "operations_email")[:90]

    fallback_extension = ".pdf" if safe_str(content_type).lower() == "application/pdf" else ""
    safe_filename = safe_storage_name(filename, f"attachment_{attachment_index}{fallback_extension}")

    stored_path = operations_attachment_storage_dir() / f"{safe_message}_{attachment_index}_{safe_filename}"
    stored_path.write_bytes(content or b"")

    try:
        attachment_text, attachment_parsed = parse_operations_attachment_bytes(
            content or b"",
            safe_filename,
            content_type,
        )
        parse_error = ""
    except Exception as exc:
        attachment_text = ""
        attachment_parsed = {}
        parse_error = str(exc)

    is_pdf = is_pdf_filename(safe_filename, content_type)

    return {
        "filename": safe_filename,
        "file_path": str(stored_path),
        "content_type": content_type or ("application/pdf" if is_pdf else "application/octet-stream"),
        "is_pdf": is_pdf,
        "parsed_data": attachment_parsed,
        "fields_found": field_count(attachment_parsed),
        "text_preview": attachment_text[:1800],
        "parse_error": parse_error,
        "size_bytes": len(content or b""),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_operations_pdf_attachment(
    *,
    content: bytes,
    filename: str,
    message_id: str,
    attachment_index: int,
) -> dict:
    return save_operations_attachment(
        content=content,
        filename=filename,
        message_id=message_id,
        attachment_index=attachment_index,
        content_type="application/pdf",
    )


@st.cache_data(show_spinner=False, ttl=900)
def read_operations_attachment_file_cached(file_path: str, modified_ns: int) -> bytes:
    return Path(file_path).read_bytes()


def read_operations_attachment_bytes(file_path: str) -> bytes:
    path = Path(file_path)
    return read_operations_attachment_file_cached(str(path), path.stat().st_mtime_ns)


def read_operations_pdf_bytes(file_path: str) -> bytes:
    return read_operations_attachment_bytes(file_path)


@st.cache_data(show_spinner=False, ttl=900)
def parse_operations_attachment_file_cached(
    file_path: str,
    filename: str,
    content_type: str,
    modified_ns: int,
) -> tuple[str, dict]:
    content = Path(file_path).read_bytes()
    return parse_operations_attachment_bytes(content, filename, content_type)


def parse_saved_operations_attachment(
    file_path: str,
    filename: str,
    content_type: str = "",
) -> tuple[str, dict]:
    path = Path(file_path)
    return parse_operations_attachment_file_cached(
        str(path),
        filename,
        content_type,
        path.stat().st_mtime_ns,
    )


@st.cache_data(show_spinner=False, ttl=900)
def parse_operations_pdf_file_cached(
    file_path: str,
    filename: str,
    modified_ns: int,
) -> tuple[str, dict]:
    content = Path(file_path).read_bytes()
    return parse_operations_pdf_bytes(content, filename)


def parse_saved_operations_pdf(file_path: str, filename: str) -> tuple[str, dict]:
    path = Path(file_path)
    return parse_operations_pdf_file_cached(str(path), filename, path.stat().st_mtime_ns)


# Compatibility aliases
_save_operations_attachment = save_operations_attachment
_save_operations_pdf_attachment = save_operations_pdf_attachment
_read_operations_pdf_file = read_operations_attachment_file_cached
_read_operations_attachment_bytes = read_operations_attachment_bytes
_read_operations_pdf_bytes = read_operations_pdf_bytes
_parse_operations_attachment_file = parse_operations_attachment_file_cached
_parse_saved_operations_attachment = parse_saved_operations_attachment
_parse_operations_pdf_file = parse_operations_pdf_file_cached
_parse_saved_operations_pdf = parse_saved_operations_pdf


# ============================================================
# Attachment extraction / merging
# ============================================================

def extract_operations_attachments(parsed: dict, record: dict | pd.Series | None = None) -> list[dict]:
    parsed = coerce_json_dict(parsed)

    attachments = parsed.get(OPERATIONS_ATTACHMENTS_KEY, [])
    if not isinstance(attachments, list):
        attachments = []

    normalized = [item for item in attachments if isinstance(item, dict)]

    for pdf_item in parsed.get(OPERATIONS_PDF_ATTACHMENTS_KEY, []) or []:
        if not isinstance(pdf_item, dict):
            continue

        pdf_path = safe_str(pdf_item.get("file_path", ""))
        already_added = any(safe_str(item.get("file_path", "")) == pdf_path for item in normalized)

        if not already_added:
            normalized.append(pdf_item)

    if record is not None:
        filename = safe_str(record.get("filename", "") if hasattr(record, "get") else "")
        file_path = safe_str(record.get("file_path", "") if hasattr(record, "get") else "")

        if filename and file_path:
            already_added = any(safe_str(item.get("file_path", "")) == file_path for item in normalized)

            if not already_added:
                normalized.append(
                    {
                        "filename": filename,
                        "file_path": file_path,
                        "content_type": "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream",
                        "is_pdf": filename.lower().endswith(".pdf"),
                        "parsed_data": {},
                        "fields_found": 0,
                        "text_preview": "",
                        "parse_error": "",
                    }
                )

    return normalized


def extract_operations_pdf_attachments(parsed: dict, record: dict | pd.Series | None = None) -> list[dict]:
    return [
        item
        for item in extract_operations_attachments(parsed, record)
        if is_pdf_filename(item.get("filename", ""), item.get("content_type", "")) or bool(item.get("is_pdf"))
    ]


def merge_operations_order_fields(body_parsed: dict, document_parsed: dict) -> tuple[dict, list[dict], list[str]]:
    final_data = {}
    rows = []
    conflicts = []

    body_parsed = coerce_json_dict(body_parsed)
    document_parsed = coerce_json_dict(document_parsed)

    for field in OPERATIONS_ORDER_FIELDS:
        body_value = safe_str(body_parsed.get(field, ""))
        document_value = safe_str(document_parsed.get(field, ""))

        if field == "Dispatcher Notes" and body_value and document_value:
            final_value = body_value if document_value in body_value else f"{body_value}\n{document_value}"
        else:
            final_value = document_value or body_value

        final_data[field] = final_value

        if field == "Dispatcher Notes" and body_value and document_value:
            status = "Combined"
        elif body_value and document_value and body_value.lower() != document_value.lower():
            status = "Review mismatch"
            conflicts.append(field)
        elif final_value:
            status = "Found"
        else:
            status = "Blank"

        rows.append(
            {
                "Field": field,
                "Email Body": body_value,
                "Document": document_value,
                "Final Value": final_value,
                "Status": status,
            }
        )

    return final_data, rows, conflicts


def merge_operations_body_parsed_fields(current: dict, reparsed: dict) -> tuple[dict, bool]:
    updated = dict(current or {})
    reparsed = coerce_json_dict(reparsed)
    changed = False

    for field in OPERATIONS_ORDER_FIELDS:
        existing_value = safe_str(updated.get(field, ""))
        incoming_value = safe_str(reparsed.get(field, ""))

        if not incoming_value:
            continue

        should_replace = not existing_value

        if field == "Container Number" and incoming_value and existing_value.upper() != incoming_value.upper():
            incoming_notes = safe_str(reparsed.get("Dispatcher Notes", ""))
            should_replace = "container correction noted" in incoming_notes.lower()

        if should_replace:
            updated[field] = incoming_value
            changed = True

    current_notes = safe_str(updated.get("Dispatcher Notes", ""))
    incoming_notes = safe_str(reparsed.get("Dispatcher Notes", ""))

    if incoming_notes and incoming_notes.lower() not in current_notes.lower():
        updated["Dispatcher Notes"] = f"{current_notes}; {incoming_notes}".strip("; ").strip()
        changed = True

    return updated, changed


def merge_saved_attachment_fields(parsed: dict, saved_attachments: list[dict]) -> dict:
    updated = dict(parsed or {})

    for attachment in saved_attachments:
        attachment_parsed = coerce_json_dict(attachment.get("parsed_data") or {})

        for field in OPERATIONS_ORDER_FIELDS:
            attachment_value = safe_str(attachment_parsed.get(field, ""))

            if not attachment_value:
                continue

            if field == "Dispatcher Notes":
                existing_value = safe_str(updated.get(field, ""))

                if existing_value and attachment_value not in existing_value:
                    updated[field] = f"{existing_value}\n{attachment_value}"
                elif not existing_value:
                    updated[field] = attachment_value

            elif not safe_str(updated.get(field, "")):
                updated[field] = attachment_value

    if saved_attachments:
        updated[OPERATIONS_ATTACHMENTS_KEY] = saved_attachments

        pdf_attachments = [
            item
            for item in saved_attachments
            if is_pdf_filename(item.get("filename", ""), item.get("content_type", "")) or bool(item.get("is_pdf"))
        ]

        if pdf_attachments:
            updated[OPERATIONS_PDF_ATTACHMENTS_KEY] = pdf_attachments

    return updated


def store_operations_parsed_data(intake_id: int, parsed_data: dict, action_required: str | None = None) -> None:
    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            action_required = coalesce(:action_required, action_required)
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": json_dump(parsed_data),
            "action_required": action_required,
        },
    )


# Compatibility aliases
_extract_operations_attachments = extract_operations_attachments
_extract_operations_pdf_attachments = extract_operations_pdf_attachments
_merge_operations_order_fields = merge_operations_order_fields
_merge_operations_body_parsed_fields = merge_operations_body_parsed_fields
_merge_saved_attachment_fields = merge_saved_attachment_fields
_store_operations_parsed_data = store_operations_parsed_data


# ============================================================
# Email item matching / mailbox backfill
# ============================================================

def extract_email_address(value: str) -> str:
    parsed = parseaddr(str(value or ""))
    return parsed[1] or str(value or "").strip()


def email_received_lookup_key(value) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    parsed = pd.to_datetime(value, errors="coerce", utc=True)

    if pd.isna(parsed):
        return safe_str(value)

    return parsed.isoformat()


def email_subject_match_key(value: str) -> str:
    text = safe_str(value).lower()

    while True:
        cleaned = re.sub(r"^\s*(?:re|fw|fwd)\s*:\s*", "", text, flags=re.I)
        if cleaned == text:
            break
        text = cleaned

    return re.sub(r"\s+", " ", text).strip()


def email_body_match_key(value: str) -> str:
    text = re.sub(r"\s+", " ", safe_str(value).lower()).strip()
    return text[:240]


def email_item_matches_operations_record(item: dict, record: dict) -> bool:
    item_message_id = safe_str(item.get("message_id") or item.get("id", ""))
    record_message_id = safe_str(record.get("source_message_id", ""))

    if item_message_id and record_message_id and item_message_id == record_message_id:
        return True

    item_subject = email_subject_match_key(item.get("subject", ""))
    record_subject = email_subject_match_key(record.get("source_subject", ""))

    if not item_subject or item_subject != record_subject:
        return False

    item_sender = extract_email_address(item.get("from", "")).lower()
    record_sender = extract_email_address(record.get("source_sender", "")).lower()

    if item_sender and record_sender and item_sender != record_sender:
        return False

    item_received = email_received_lookup_key(item.get("received_at"))
    record_received = email_received_lookup_key(record.get("source_received_at"))

    if item_received and record_received and item_received == record_received:
        return True

    item_body = email_body_match_key(item.get("body", ""))
    record_body = email_body_match_key(record.get("raw_text", ""))

    return bool(item_body and record_body and (item_body in record_body or record_body in item_body))


def backfill_operations_email_attachments(
    *,
    existing_record: dict,
    email_item: dict,
    message_id: str,
) -> int:
    parsed = coerce_json_dict(existing_record.get("parsed_data"))
    existing_attachments = extract_operations_attachments(parsed, existing_record)

    existing_names = {
        safe_str(item.get("filename", "")).lower()
        for item in existing_attachments
    }

    existing_paths = {
        safe_str(item.get("file_path", ""))
        for item in existing_attachments
    }

    new_attachments = []

    for attachment_index, attachment in enumerate(email_item.get("attachments", []) or [], start=1):
        filename = safe_str(attachment.get("filename", ""))
        content = attachment.get("content") or b""
        content_type = safe_str(attachment.get("content_type", ""))

        if not filename or not content:
            continue

        if filename.lower() in existing_names:
            continue

        saved = save_operations_attachment(
            content=content,
            filename=filename,
            message_id=message_id or f"intake-{existing_record.get('id')}",
            attachment_index=len(existing_attachments) + len(new_attachments) + attachment_index,
            content_type=content_type,
        )

        if safe_str(saved.get("file_path", "")) in existing_paths:
            continue

        new_attachments.append(saved)

    if not new_attachments:
        return 0

    merged_attachments = existing_attachments + new_attachments
    updated_parsed = merge_saved_attachment_fields(parsed, merged_attachments)

    primary = next(
        (
            item
            for item in merged_attachments
            if is_pdf_filename(item.get("filename", ""), item.get("content_type", ""))
        ),
        merged_attachments[0],
    )

    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            filename = coalesce(filename, :filename),
            file_path = coalesce(file_path, :file_path),
            action_required = case
                when coalesce(action_required, '') = '' then :action_required
                else action_required
            end
        where id = :intake_id
        """,
        {
            "intake_id": int(existing_record["id"]),
            "parsed_data": json_dump(updated_parsed),
            "filename": primary.get("filename"),
            "file_path": primary.get("file_path"),
            "action_required": order_action_required_from_parsed(updated_parsed),
        },
    )

    return len(new_attachments)


def backfill_operations_pdf_attachments(
    *,
    existing_record: dict,
    email_item: dict,
    message_id: str,
) -> int:
    return backfill_operations_email_attachments(
        existing_record=existing_record,
        email_item=email_item,
        message_id=message_id,
    )


def rescan_operations_request_attachments(
    record,
    fetch_by_message_id=None,
    fetch_recent_emails=None,
    limit: int = 250,
) -> dict:
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})

    result = {
        "checked": 0,
        "matched": 0,
        "source_attachment_count": 0,
        "saved": 0,
        "attachment_names": [],
    }

    message_id = safe_str(record_dict.get("source_message_id", ""))

    emails = []

    if message_id and callable(fetch_by_message_id):
        try:
            emails.extend(fetch_by_message_id(message_id, limit=5))
        except Exception:
            pass

    exact_matches = [
        item
        for item in emails
        if email_item_matches_operations_record(item, record_dict)
    ]

    if exact_matches:
        emails = exact_matches
    elif callable(fetch_recent_emails):
        try:
            emails = fetch_recent_emails(limit=limit)
        except Exception:
            emails = []

    result["checked"] = len(emails)

    for item in emails:
        if not email_item_matches_operations_record(item, record_dict):
            continue

        result["matched"] += 1

        attachments = item.get("attachments", []) or []
        result["source_attachment_count"] += len(attachments)

        result["attachment_names"].extend(
            [
                safe_str(attachment.get("filename", ""))
                for attachment in attachments
                if safe_str(attachment.get("filename", ""))
            ]
        )

        message_id = safe_str(item.get("message_id") or item.get("id") or record_dict.get("source_message_id", ""))

        result["saved"] += backfill_operations_email_attachments(
            existing_record=record_dict,
            email_item=item,
            message_id=message_id or f"intake-{record_dict.get('id')}",
        )

    result["attachment_names"] = sorted(set(result["attachment_names"]))

    return result


def order_action_required_from_parsed(parsed: dict) -> str:
    missing = []

    for field in ["Booking Number", "Customer", "Warehouse"]:
        if not safe_str(parsed.get(field, "")):
            missing.append(field)

    if missing:
        return "Missing order details: " + ", ".join(missing)

    return "New booking details found; review parsed fields before creating order."


# Compatibility aliases
_extract_email_address = extract_email_address
_email_received_lookup_key = email_received_lookup_key
_email_subject_match_key = email_subject_match_key
_email_body_match_key = email_body_match_key
_email_item_matches_operations_record = email_item_matches_operations_record
_backfill_operations_email_attachments = backfill_operations_email_attachments
_backfill_operations_pdf_attachments = backfill_operations_pdf_attachments
_rescan_operations_request_attachments = rescan_operations_request_attachments
_order_action_required_from_parsed = order_action_required_from_parsed


# ============================================================
# Load update / document attachment helpers
# ============================================================

def update_load_from_operations_parsed(
    load_id,
    parsed: dict,
    overwrite: bool = False,
) -> dict:
    load_id = int_or_none(load_id)
    parsed = coerce_json_dict(parsed)

    result = {
        "load_id": load_id,
        "updated_fields": [],
        "skipped_fields": [],
        "error": "",
    }

    if load_id is None:
        result["error"] = "Missing load id."
        return result

    load_columns = existing_load_columns()

    mapped_updates = {}

    for parsed_field, load_column in PARSED_TO_LOAD_COLUMN_MAP.items():
        if load_column not in load_columns:
            continue

        value = safe_str(parsed.get(parsed_field, ""))

        if not value:
            continue

        mapped_updates[load_column] = value

    if not mapped_updates:
        return result

    try:
        current_df = read_df(
            f"""
            select {", ".join(["id"] + list(mapped_updates.keys()))}
            from loads
            where id = :load_id
            limit 1
            """,
            {"load_id": load_id},
        )
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if current_df.empty:
        result["error"] = "Load not found."
        return result

    current = current_df.iloc[0].to_dict()
    final_updates = {}

    for column, value in mapped_updates.items():
        existing_value = safe_str(current.get(column, ""))

        if existing_value and not overwrite:
            result["skipped_fields"].append(column)
            continue

        final_updates[column] = value

    if not final_updates:
        return result

    set_clause = ", ".join([f"{column} = :{column}" for column in final_updates.keys()])
    params = dict(final_updates)
    params["load_id"] = load_id

    try:
        execute(
            f"""
            update loads
            set {set_clause},
                updated_at = now()
            where id = :load_id
            """,
            params,
        )
    except Exception as exc:
        result["error"] = str(exc)
        return result

    result["updated_fields"] = list(final_updates.keys())
    return result


def update_load_from_operations_pdf(
    load_id,
    parsed: dict,
    overwrite: bool = False,
) -> dict:
    return update_load_from_operations_parsed(load_id, parsed, overwrite=overwrite)


def attach_saved_operations_file_to_load(
    *,
    load_id,
    attachment: dict,
    document_type: str = "Operations Attachment",
) -> dict:
    load_id = int_or_none(load_id)

    result = {
        "load_id": load_id,
        "attached": False,
        "document_id": None,
        "error": "",
    }

    if load_id is None:
        result["error"] = "Missing load id."
        return result

    filename = safe_str(attachment.get("filename", ""))
    file_path = safe_str(attachment.get("file_path", ""))

    if not filename or not file_path:
        result["error"] = "Attachment is missing filename or file path."
        return result

    try:
        execute(
            """
            insert into documents (
                load_id,
                document_type,
                filename,
                file_path,
                created_at
            )
            values (
                :load_id,
                :document_type,
                :filename,
                :file_path,
                now()
            )
            """,
            {
                "load_id": load_id,
                "document_type": document_type,
                "filename": filename,
                "file_path": file_path,
            },
        )

        result["attached"] = True
        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result


def attach_saved_pdf_to_load(
    *,
    load_id,
    attachment: dict,
    document_type: str = "Operations PDF",
) -> dict:
    return attach_saved_operations_file_to_load(
        load_id=load_id,
        attachment=attachment,
        document_type=document_type,
    )


def import_uploaded_operations_attachment(
    *,
    uploaded_file,
    message_id: str,
    attachment_index: int = 1,
) -> dict:
    filename = safe_str(getattr(uploaded_file, "name", "")) or f"uploaded_attachment_{attachment_index}"
    content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    content_type = safe_str(getattr(uploaded_file, "type", ""))

    return save_operations_attachment(
        content=content or b"",
        filename=filename,
        message_id=message_id,
        attachment_index=attachment_index,
        content_type=content_type,
    )


# Compatibility aliases
_update_load_from_operations_parsed = update_load_from_operations_parsed
_update_load_from_operations_pdf = update_load_from_operations_pdf
_attach_saved_operations_file_to_load = attach_saved_operations_file_to_load
_attach_saved_pdf_to_load = attach_saved_pdf_to_load
_import_uploaded_operations_attachment = import_uploaded_operations_attachment