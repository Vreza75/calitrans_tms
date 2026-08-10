"""parse_operations_pdf_bytes (and its .docx/.txt siblings) aliased
hybrid_doc_result["parsed_fields"] instead of copying it, then wrote
parsed["_hybrid_document_parser"] = hybrid_doc_result back onto that same
object - a genuine circular reference (parsed_fields -> _hybrid_document_parser
-> hybrid_doc_result -> parsed_fields, same object). This never surfaced
before because quick sync never actually fetched a real attachment for the
parser to run against; it crashes json.dumps with "Circular reference
detected" the moment a real PDF is saved via save_operations_attachment.
"""
import json

from services import operations_attachment_service as attachment_service


def test_parse_operations_pdf_bytes_does_not_create_circular_reference(monkeypatch):
    fake_hybrid_result = {
        "parser_used": "rule_parser",
        "confidence": 0.95,
        "needs_review": False,
        "parsed_fields": {"Customer": "Flat World", "Booking Number": "130067971"},
        "rule_parser_output": {"Customer": "Flat World", "Booking Number": "130067971"},
        "document_parser_agent": {},
        "warnings": [],
    }
    monkeypatch.setattr(attachment_service, "extract_text_from_pdf", lambda pdf_file: "some extracted pdf text")
    monkeypatch.setattr(attachment_service, "parse_document_hybrid", lambda **kwargs: fake_hybrid_result)

    text, parsed = attachment_service.parse_operations_pdf_bytes(b"%PDF-1.4 fake", "rate_confirmation.pdf")

    assert parsed["Customer"] == "Flat World"
    assert parsed["_hybrid_document_parser"]["parser_used"] == "rule_parser"
    # Must not raise "Circular reference detected".
    json.dumps(parsed, default=str)


def test_save_operations_attachment_produces_json_serializable_parsed_data(monkeypatch):
    fake_hybrid_result = {
        "parser_used": "rule_parser",
        "confidence": 0.95,
        "needs_review": False,
        "parsed_fields": {"Customer": "Flat World", "Booking Number": "130067971"},
        "rule_parser_output": {"Customer": "Flat World", "Booking Number": "130067971"},
        "document_parser_agent": {},
        "warnings": [],
    }
    monkeypatch.setattr(attachment_service, "extract_text_from_pdf", lambda pdf_file: "some extracted pdf text")
    monkeypatch.setattr(attachment_service, "parse_document_hybrid", lambda **kwargs: fake_hybrid_result)
    monkeypatch.setattr(
        attachment_service,
        "operations_attachment_storage_dir",
        lambda: __import__("pathlib").Path(__import__("tempfile").gettempdir()),
    )

    saved = attachment_service.save_operations_attachment(
        content=b"%PDF-1.4 fake",
        filename="rate_confirmation.pdf",
        message_id="test-message-1",
        attachment_index=1,
        content_type="application/pdf",
    )

    assert saved["parse_error"] == ""
    # This is exactly what backfill_operations_email_attachments does with
    # the saved attachment before writing it to order_intake.parsed_data.
    json.dumps(saved["parsed_data"], default=str)
