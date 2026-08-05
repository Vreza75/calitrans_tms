"""Regression coverage for the edge-design rework identified by an
independent Codex review of fix/operations-inbox-certification-edge-corrections:
quote-intent/detail scope mixing, destructive history clipping, address
continuation absorbing unrelated fields, and Full Return Terminal being lost
across the attachment-reconciliation / load-create / load-update paths.

See docs/reviews/OPERATIONS_INBOX_CERTIFICATION_EDGE_REWORK.md.
"""
import pytest

from services.email_parser import parse_email_text
from services.message_scope import build_message_scope
from services.operations_attachment_service import (
    OPERATIONS_ORDER_FIELDS as ATTACHMENT_ORDER_FIELDS,
    PARSED_TO_LOAD_COLUMN_MAP,
    merge_saved_attachment_fields,
)
from services.operations_inbox_service import classify_customer_request


# --- A. Quote intent must not combine with details from a different scope --


def test_current_operational_update_with_old_quoted_quote_intent_is_not_quote_request():
    body = (
        "Please update delivery for container ABCU1234567.\n"
        "Size: 40HC\n"
        "Delivery Date: August 8\n\n"
        "On July 1, Customer wrote:\n"
        "Please quote Houston to Dallas.\n"
    )
    result = classify_customer_request("Update", body)
    assert result != "Quote Request"


# --- B. Active operational From/To lane must survive segmentation ----------


def test_active_from_to_lane_is_not_clipped_as_quoted_history():
    body = "Please quote this shipment.\n\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n"
    result = classify_customer_request("Quote", body)
    assert result == "Quote Request"


# --- C. Forwarded-only active request must remain classifiable -------------


def test_forwarded_only_request_remains_classifiable():
    body = (
        "-----Original Message-----\n"
        "From: Customer Name <customer@example.com>\n"
        "Sent: Tuesday, August 4, 2026\n"
        "To: Operations\n"
        "Subject: Rate Request\n\n"
        "Please quote Houston to Dallas.\n"
        "Equipment: 40HC\n"
    )
    result = classify_customer_request("Fwd: Rate Request", body)
    assert result == "Quote Request"


# --- D. Address extraction must stop at every operational field boundary ---


def test_address_stops_before_empty_pickup_and_signature():
    body = (
        "Delivery Address:\n"
        "200 Customer Street\n"
        "Houston, TX 77001\n"
        "Empty Pickup: ConGlobal\n"
        "Pickup Date: August 7\n"
        "John Smith\n"
        "john@example.com\n"
    )
    parsed = parse_email_text("", body)
    assert parsed["Address"] == "200 Customer Street, Houston, TX 77001"
    assert parsed["Empty Pickup"] == "ConGlobal"
    assert parsed["Pickup Date"] == "August 7"
    assert "John Smith" not in parsed["Address"]
    assert "john@example.com" not in parsed["Address"]


# --- E. Full Return Terminal must survive every production path ------------


def test_full_return_terminal_is_included_in_attachment_reconciliation_fields():
    assert "Full Return Terminal" in ATTACHMENT_ORDER_FIELDS


def test_full_return_terminal_survives_attachment_only_reconciliation():
    parsed = {"Full Return Terminal": ""}
    attachments = [{"parsed_data": {"Full Return Terminal": "Bayport Terminal"}}]
    merged = merge_saved_attachment_fields(parsed, attachments, force=True)
    assert merged["Full Return Terminal"] == "Bayport Terminal"


def test_full_return_terminal_is_mapped_for_existing_load_updates():
    assert PARSED_TO_LOAD_COLUMN_MAP.get("Full Return Terminal") == "full_return_terminal"


def test_full_return_terminal_is_mapped_for_new_load_creation():
    from db_client import SM_TO_DB_COLUMNS

    assert SM_TO_DB_COLUMNS.get("Full Return Terminal") == "full_return_terminal"


# --- Message-scope: history formats and edge shapes -------------------------


def test_gmail_wrote_marker_excludes_quoted_history():
    body = "Current reply text.\n\nOn Tue, Aug 4, 2026 at 2:15 PM Customer <x@example.com> wrote:\n> old content\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "reply"
    assert "old content" not in scope.classification_text
    assert "Current reply text" in scope.classification_text


def test_outlook_full_header_block_excludes_history():
    body = (
        "Current reply\n\n"
        "From: Customer\n"
        "Sent: Tuesday, August 4, 2026\n"
        "To: Operations\n"
        "Subject: Previous subject\n\n"
        "Old content\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "reply"
    assert "Old content" not in scope.classification_text


def test_spanish_reply_header_is_recognized():
    body = "Respuesta actual.\n\nDe: Cliente\nEnviado: martes\nPara: Operaciones\nAsunto: Anterior\n\nContenido anterior\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "reply"
    assert "Contenido anterior" not in scope.classification_text


def test_single_from_line_alone_is_not_sufficient_to_declare_history():
    body = "Please see attached.\n\nFrom: Warehouse Team\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Warehouse Team" in scope.classification_text


def test_natural_text_containing_from_word_is_not_misread_as_header():
    body = "This shipment moves from Houston to the port on Friday."
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Houston" in scope.classification_text


def test_forwarded_new_order_remains_classifiable():
    body = (
        "-----Original Message-----\n"
        "From: Customer <customer@example.com>\n"
        "Sent: Monday\n"
        "To: Dispatch\n"
        "Subject: New Booking\n\n"
        "Please create a new import order. Booking Number: ABC123. Container Number: MSCU1234567.\n"
    )
    result = classify_customer_request("Fwd: New Booking", body)
    assert result == "New Booking"


def test_nested_forward_then_reply_clips_at_first_marker():
    body = (
        "-----Original Message-----\n"
        "From: Customer <customer@example.com>\n"
        "Sent: Monday\n"
        "To: Dispatch\n"
        "Subject: Rate Request\n\n"
        "Please quote Houston to Dallas.\n"
        "Equipment: 40HC\n\n"
        "On Sunday, Someone <else@example.com> wrote:\n"
        "> irrelevant older content\n"
    )
    scope = build_message_scope(body)
    assert "irrelevant older content" not in scope.classification_text
    assert "Houston" in scope.classification_text


# --- Address boundaries: broader set of stop conditions --------------------


@pytest.mark.parametrize(
    "trailer",
    [
        "Full Return: Bayport Terminal",
        "Port: Bayport Container Terminal",
        "Booking Number: RICGX1235800",
        "Reference Number: SO217089A",
        "Contact: Jane Doe",
        "Notes: fragile cargo",
        "LFD: August 9",
    ],
)
def test_address_stops_before_various_field_labels(trailer):
    body = f"Delivery Address: 200 Customer Street\nHouston, TX 77001\n{trailer}\n"
    parsed = parse_email_text("", body)
    assert parsed["Address"] == "200 Customer Street, Houston, TX 77001"


def test_address_with_suite_and_country_line():
    body = "Delivery Address:\n200 Customer Street\nSuite 400\nHouston, TX 77001\nUnited States\n"
    parsed = parse_email_text("", body)
    assert parsed["Address"] == "200 Customer Street, Suite 400, Houston, TX 77001, United States"


def test_address_followed_by_phone_number_is_excluded():
    body = "Delivery Address: 200 Customer Street\nHouston, TX 77001\n713-555-0101\n"
    parsed = parse_email_text("", body)
    assert parsed["Address"] == "200 Customer Street, Houston, TX 77001"


# --- Full Return isolation --------------------------------------------------


def test_full_return_still_does_not_populate_empty_pickup():
    parsed = parse_email_text("", "FULL RETURN: Bayport Terminal")
    assert parsed["Empty Pickup"] == ""
    assert parsed["Full Return Terminal"] == "Bayport Terminal"


# --- Identity: valid/invalid existing combined with invalid document -------


def test_identity_valid_existing_protected_from_invalid_document():
    parsed = {"Contact Name": "Dana Phillips"}
    attachments = [{"parsed_data": {"Contact Name": "Steamship Line: CMA CGM"}}]
    merged = merge_saved_attachment_fields(parsed, attachments, force=True)
    assert merged["Contact Name"] == "Dana Phillips"


def test_identity_invalid_existing_and_invalid_document_yields_no_valid_value():
    # Neither existing nor document value is a valid Contact Name here -
    # an invalid document value can't "win" over an invalid existing one,
    # since neither ever validates as a usable source.
    parsed = {"Contact Name": "Steamship Line: CMA CGM"}
    attachments = [{"parsed_data": {"Contact Name": "Carrier: ONE"}}]
    merged = merge_saved_attachment_fields(parsed, attachments, force=True)
    assert merged["Contact Name"] != "Steamship Line: CMA CGM"
    assert merged["Contact Name"] != "Carrier: ONE"
