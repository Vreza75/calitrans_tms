from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INBOX_SOURCE = (ROOT / "pages_app" / "operations_inbox.py").read_text(encoding="utf-8")
SERVICE_SOURCE = (ROOT / "services" / "operations_inbox_service.py").read_text(encoding="utf-8")
ADMIN_SOURCE = (ROOT / "pages_app" / "email_imports.py").read_text(encoding="utf-8")


def _function_source(
    source: str,
    function_name: str,
    next_function_name: str | None,
) -> str:
    start = source.index(f"def {function_name}")
    end = (
        source.index(f"def {next_function_name}", start)
        if next_function_name
        else len(source)
    )
    return source[start:end]


def test_dispatcher_uses_read_only_source_document_preview() -> None:
    assert "_render_operations_attachment_preview(" in INBOX_SOURCE
    assert "##### Source Documents" in SERVICE_SOURCE
    assert "Download Original" in SERVICE_SOURCE
    assert "No source documents are available" in SERVICE_SOURCE


def test_dispatcher_preview_has_no_recovery_or_parser_controls() -> None:
    preview = _function_source(
        SERVICE_SOURCE,
        "render_operations_attachment_preview",
        "render_operations_pdf_panel",
    )
    for forbidden in (
        "Check Source Email for Attachments",
        "Add attachment to this operations request",
        "Import Uploaded Attachment",
        "Parse / Reparse",
        "Fields Found",
        "render_document_review_panel",
        "rescan_operations_request_attachments",
        "import_uploaded_operations_attachment",
        "parse_saved_operations_attachment",
    ):
        assert forbidden not in preview


def test_admin_owns_full_attachment_diagnostics() -> None:
    assert "Document Parsing & Attachment Diagnostics" in ADMIN_SOURCE
    for required in (
        "Check Source Email for Attachments",
        "Manually Add Missing Attachment",
        "Parse / Reparse",
        "Fields Found",
        "render_operations_pdf_panel",
    ):
        assert required in ADMIN_SOURCE + SERVICE_SOURCE


def test_work_item_open_does_not_reparse_or_persist_parsed_data() -> None:
    work_item = _function_source(
        INBOX_SOURCE,
        "_render_selected_operations_work_item",
        None,
    )
    assert "parse_email_body_hybrid(" not in work_item
    assert "_store_operations_parsed_data(" not in work_item
    assert "rescan_operations_request_attachments(" not in work_item
    assert "parse_saved_operations_attachment(" not in work_item
    assert "sync_operations_email_engine(" not in work_item
    assert "auto_classify_open_inbox_items(" not in work_item


def test_collapsed_expensive_sections_are_dynamically_guarded() -> None:
    assert 'st.expander("Communication History", on_change="rerun")' in INBOX_SOURCE
    assert "if history_expander.open:" in INBOX_SOURCE
    assert 'st.expander("Active Pending Order Draft", on_change="rerun")' in INBOX_SOURCE
    assert "if not pending_draft_expander.open:" in INBOX_SOURCE


def test_automatic_ingestion_still_saves_and_reconciles_attachments() -> None:
    prepare_record = _function_source(
        SERVICE_SOURCE,
        "_prepare_operations_email_record",
        "_insert_operations_email_message",
    )
    assert "_save_operations_email_attachments(message, message_id)" in prepare_record
    assert "merge_saved_attachment_fields(parsed, saved_attachments, force=True)" in prepare_record


def test_dispatcher_preview_extracts_attachment_metadata_once() -> None:
    preview = _function_source(
        SERVICE_SOURCE,
        "render_operations_attachment_preview",
        "render_operations_pdf_panel",
    )
    assert preview.count("group_operations_source_documents(") == 1


def test_full_queue_rerender_is_instrumented_for_initial_open() -> None:
    assert '"full_queue_rerender"' in INBOX_SOURCE
    assert '"button_to_dialog_render"' in INBOX_SOURCE
    assert "WORK_ITEM_OPEN_STARTED_AT" in INBOX_SOURCE
