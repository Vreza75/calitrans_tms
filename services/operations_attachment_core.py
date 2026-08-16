# services/operations_attachment_core.py

"""
Framework-neutral attachment core.

No streamlit import, directly or transitively - unlike
services/operations_attachment_service.py (which also contains PDF/DOCX
parsing via ai_agents.hybrid_document_parser, @st.cache_data-wrapped file
reads, and render_* UI functions), this module only has metadata
extraction/grouping and raw byte access. Codex's Phase 1 correction audit
flagged that application.work_items.queries lazy-imported
operations_attachment_service specifically to dodge its streamlit
coupling - this module is the real fix: the application layer can import
it at module top, no lazy-import trick needed, because there is nothing
streamlit-coupled to dodge.

services/operations_attachment_service.py re-exports the names below (see
its own "Framework-neutral core (see operations_attachment_core.py)"
section) so every existing caller of e.g.
operations_attachment_service.extract_operations_attachments keeps
working unchanged - one canonical implementation, not a duplicate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from utils.text_helpers import safe_str

OPERATIONS_ATTACHMENTS_KEY = "_operations_attachments"
OPERATIONS_PDF_ATTACHMENTS_KEY = "_operations_pdf_attachments"


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


def is_pdf_filename(filename: str, content_type: str = "") -> bool:
    return (
        safe_str(filename).lower().endswith(".pdf")
        or safe_str(content_type).lower() == "application/pdf"
    )


def read_attachment_bytes(file_path: str) -> bytes:
    """Uncached raw byte read. The Streamlit-facing
    operations_attachment_service.read_operations_attachment_bytes() wraps
    this behind an @st.cache_data layer for repeat reads within one
    Streamlit process; the application/API path calls this directly since
    it doesn't have (or need) Streamlit's rerun-driven cache."""
    return Path(file_path).read_bytes()


def attachment_ref(file_path: str) -> str:
    """Opaque, stable reference derived from a server file_path - exposed
    to API/UI clients instead of the path itself. Deterministic (sha256),
    so the same path always maps to the same ref without a lookup table."""
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:24] if file_path else ""


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


def group_operations_source_documents(
    current_record,
    current_parsed: dict,
    timeline_rows: pd.DataFrame | list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Separate exact-message attachments from earlier conversation documents."""
    record_dict = (
        current_record.to_dict()
        if hasattr(current_record, "to_dict")
        else dict(current_record or {})
    )
    current_id = safe_str(record_dict.get("id"))
    current_message_id = safe_str(record_dict.get("source_message_id"))

    def with_source_metadata(attachment: dict, source: dict) -> dict:
        item = dict(attachment or {})
        item["source_work_item_id"] = source.get("id")
        item["source_message_id"] = (
            safe_str(item.get("source_message_id"))
            or safe_str(source.get("source_message_id"))
        )
        item["source_subject"] = safe_str(source.get("source_subject"))
        item["source_received_at"] = safe_str(source.get("source_received_at"))
        return item

    current = [
        with_source_metadata(item, record_dict)
        for item in extract_operations_attachments(current_parsed, record_dict)
    ]
    current_identities = {
        (
            safe_str(item.get("source_message_id")),
            safe_str(item.get("content_sha256"))
            or safe_str(item.get("file_path")),
        )
        for item in current
    }
    prior: list[dict] = []
    prior_identities: set[tuple[str, str]] = set()

    if isinstance(timeline_rows, pd.DataFrame):
        rows = timeline_rows.to_dict(orient="records")
    else:
        rows = list(timeline_rows or [])

    for row in rows:
        row_dict = dict(row or {})
        row_id = safe_str(row_dict.get("id"))
        row_message_id = safe_str(row_dict.get("source_message_id"))
        if (current_id and row_id == current_id) or (
            current_message_id and row_message_id == current_message_id
        ):
            continue
        row_parsed = coerce_json_dict(row_dict.get("parsed_data"))
        for attachment in extract_operations_attachments(row_parsed, row_dict):
            item = with_source_metadata(attachment, row_dict)
            identity = (
                safe_str(item.get("source_message_id")),
                safe_str(item.get("content_sha256"))
                or safe_str(item.get("file_path")),
            )
            if identity in current_identities or identity in prior_identities:
                continue
            prior_identities.add(identity)
            prior.append(item)

    return {"current": current, "prior": prior}
