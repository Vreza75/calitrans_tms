"""Regression coverage for the two HIGH-severity message_scope.py defects
found by an independent Codex review of
fix/operations-inbox-certification-edge-rework (HEAD 98e1d8e):

1. An operational From/To lane block containing a Date/Sent/Subject-style
   label nearby was still misdetected as reply-header metadata, discarding
   the entire lane (including Equipment/Rate/etc.) as if it were quoted
   history.
2. Forwarded-header block stripping only removed the first header line
   (From:); Sent:/To:/Subject:/Cc:/Bcc:/Date: leaked into
   scope.classification_text for every forwarded_only message.

See docs/reviews/OPERATIONS_INBOX_MESSAGE_SCOPE_FINAL_CORRECTIONS.md.
"""
import pytest

from services.email_parser import parse_email_text
from services.message_scope import build_message_scope
from services.operations_inbox_service import classify_customer_request


# =====================================================================
# Defect 1 - operational lane blocks misdetected as reply headers
# =====================================================================


def test_from_to_date_equipment_lane_is_not_clipped_as_reply():
    body = (
        "Please quote:\n\n"
        "From: Houston\n"
        "To: Dallas\n"
        "Date: August 10\n"
        "Equipment: 40HC\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "From: Houston" in scope.classification_text
    assert "To: Dallas" in scope.classification_text
    assert "Date: August 10" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text
    assert classify_customer_request("Quote", body) == "Quote Request"


def test_from_to_pickup_date_equipment_lane_is_not_clipped():
    body = (
        "Please quote:\n\n"
        "From: Houston\n"
        "To: Dallas\n"
        "Pickup Date: August 10\n"
        "Equipment: 40HC\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Equipment: 40HC" in scope.classification_text


def test_from_to_delivery_date_rate_lane_is_not_clipped():
    body = (
        "Please quote:\n\n"
        "From: Bayport Terminal\n"
        "To: Katy Warehouse\n"
        "Delivery Date: August 11\n"
        "Rate requested\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Rate requested" in scope.classification_text


def test_from_to_subject_equipment_lane_is_not_clipped():
    body = (
        "Please quote:\n\n"
        "From: Houston\n"
        "To: Dallas\n"
        "Subject: Container Quote\n"
        "Equipment: 40HC\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Equipment: 40HC" in scope.classification_text


def test_from_to_container_number_lane_is_not_clipped():
    body = "Please quote:\n\nFrom: Houston\nTo: Dallas\nContainer Number: MSCU1234567\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Container Number: MSCU1234567" in scope.classification_text


def test_from_to_booking_number_lane_is_not_clipped():
    body = "Please quote:\n\nFrom: Houston\nTo: Dallas\nBooking Number: ABC123\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Booking Number: ABC123" in scope.classification_text


def test_from_to_port_lane_is_not_clipped():
    body = "Please quote:\n\nFrom: Houston\nTo: Dallas\nPort: Bayport Container Terminal\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Port: Bayport Container Terminal" in scope.classification_text


def test_spanish_operational_lane_is_not_clipped():
    body = "Por favor cotizar:\n\nDe: Houston\nA: Dallas\nFecha: 10 de agosto\nEquipo: 40HC\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Equipo: 40HC" in scope.classification_text


def test_real_outlook_header_with_date_still_recognized_as_reply():
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


def test_from_with_email_address_remains_valid_reply_header():
    body = "Current.\n\nFrom: Customer <customer@example.com>\nOld body\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "reply"
    assert "Old body" not in scope.classification_text


def test_bare_date_without_from_does_not_become_history():
    body = "Booking confirmation.\nDate: August 7\nBooking Number: ABC123\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    assert "Booking Number: ABC123" in scope.classification_text


def test_ricgx1235800_booking_fixture_still_parses_correctly():
    body = (
        "NUMBER OF CNTRS: 4\n"
        "40HC\n"
        "Date: 02-Jul-26 10:22\n"
        "FULL RETURN: Bayport Terminal\n"
    )
    parsed = parse_email_text("", body)
    assert parsed.get("Full Return Terminal") == "Bayport Terminal"


def test_person_name_from_to_pair_is_not_a_quote_lane_alone():
    body = "From: John Smith\nTo: Maria Garcia\n"
    scope = build_message_scope(body)
    assert scope.scope_type == "new_message"
    result = classify_customer_request("", body)
    assert result != "Quote Request"


def test_weekday_from_to_pair_remains_rejected():
    body = "Please quote this.\n\nFrom: Monday\nTo: Friday\n"
    result = classify_customer_request("Quote", body)
    assert result != "Quote Request"


def test_time_of_day_from_to_pair_remains_rejected():
    body = "Please quote this.\n\nFrom: 8 AM\nTo: 5 PM\n"
    result = classify_customer_request("Quote", body)
    assert result != "Quote Request"


def test_operational_lane_without_quote_intent_does_not_force_quote_request():
    body = "From: Houston\nTo: Dallas\n"
    result = classify_customer_request("Update", body)
    assert result != "Quote Request"


# =====================================================================
# Defect 2 - forwarded header block leakage
# =====================================================================


def test_forwarded_only_strips_complete_english_header_block():
    body = (
        "FYI\n\n"
        "-----Original Message-----\n"
        "From: Customer Name <customer@example.com>\n"
        "Sent: Monday, August 4, 2026\n"
        "To: Operations <ops@example.com>\n"
        "Subject: Rate Request\n\n"
        "Please quote Houston to Dallas.\n"
        "Equipment: 40HC\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "forwarded_only"
    for header in ["Sent:", "To:", "Subject:", "Cc:", "Bcc:", "Date:", "From:"]:
        assert header not in scope.classification_text, f"{header} leaked into classification_text"
    assert "Please quote Houston to Dallas." in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text
    assert classify_customer_request("Fwd: Rate Request", body) == "Quote Request"


def test_forwarded_only_strips_spanish_header_block():
    body = (
        "Favor atender\n\n"
        "----- Mensaje original -----\n"
        "De: Cliente <cliente@example.com>\n"
        "Enviado: lunes, 4 de agosto de 2026\n"
        "Para: Operaciones\n"
        "Asunto: Solicitud de tarifa\n\n"
        "Por favor cotizar Houston a Dallas.\n"
        "Equipo: 40HC\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "forwarded_only"
    for header in ["De:", "Enviado:", "Para:", "Asunto:", "Fecha:"]:
        assert header not in scope.classification_text, f"{header} leaked into classification_text"
    assert "Por favor cotizar Houston a Dallas." in scope.classification_text


def test_forwarded_header_block_without_cc_bcc_fully_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\n"
        "Sent: Mon\n"
        "To: Ops\n"
        "Subject: Rate\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = build_message_scope(body)
    for header in ["Sent:", "To:", "Subject:"]:
        assert header not in scope.classification_text


def test_forwarded_header_block_without_sent_fully_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\n"
        "To: Ops\n"
        "Subject: Rate\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = build_message_scope(body)
    for header in ["To:", "Subject:"]:
        assert header not in scope.classification_text


def test_forwarded_header_block_without_subject_fully_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\n"
        "Sent: Mon\n"
        "To: Ops\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = build_message_scope(body)
    for header in ["Sent:", "To:"]:
        assert header not in scope.classification_text


def test_forwarded_header_block_with_blank_lines_between_fields_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\n\n"
        "Sent: Mon\n\n"
        "To: Ops\n\n"
        "Subject: Rate\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = build_message_scope(body)
    for header in ["Sent:", "To:", "Subject:"]:
        assert header not in scope.classification_text
    assert "Please quote Houston to Dallas." in scope.classification_text


def test_forwarded_new_booking_headers_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: New Booking\n\n"
        "Booking Number: ABC123. Container Number: MSCU1234567.\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text
    assert classify_customer_request("Fwd: New Booking", body) == "New Booking"


def test_forwarded_cancellation_headers_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Cancel\n\n"
        "Please cancel booking ABC123.\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text
    assert "Sent:" not in scope.classification_text


def test_forwarded_existing_load_update_headers_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Update\n\n"
        "Please update ETA for booking ABC123.\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text


def test_nested_reply_inside_forward_headers_still_stripped():
    body = (
        "\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n\n"
        "On Sunday, Someone <else@example.com> wrote:\n> irrelevant older content\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text
    assert "Sent:" not in scope.classification_text
    assert "irrelevant older content" not in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_forwarded_operational_from_to_lane_after_envelope_headers_preserved():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\n"
        "From: Houston\nTo: Dallas\nEquipment: 40HC\n"
    )
    scope = build_message_scope(body)
    for header in ["Sent:", "Subject:"]:
        assert header not in scope.classification_text
    assert "From: Houston" in scope.classification_text
    assert "To: Dallas" in scope.classification_text


def test_forwarded_subject_quote_request_but_no_active_quote_details_is_not_quote_request():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Quote Request\n\n"
        "Please confirm receipt of this document, no action otherwise needed.\n"
    )
    result = classify_customer_request("", body)
    assert result != "Quote Request"


def test_forwarded_subject_cancellation_but_active_body_is_booking_not_misled():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Cancellation\n\n"
        "Please book new import order. Booking Number: ABC123. Container Number: MSCU1234567.\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text


def test_no_leaked_metadata_contributes_intent_signals():
    """A forwarded envelope Subject line containing quote language must not
    leak in and inflate intent scoring when the forwarded body itself has no
    active quote details."""
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Please Quote This\n\n"
        "Just checking in, no update needed.\n"
    )
    scope = build_message_scope(body)
    assert "Subject:" not in scope.classification_text
    assert "Please Quote This" not in scope.classification_text


def test_meaningful_top_level_cancellation_plus_forwarded_booking_stays_authoritative():
    body = (
        "Please cancel this booking, customer changed their mind.\n\n"
        "-----Original Message-----\n"
        "From: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: New Booking\n\n"
        "Booking Number: ABC123 Container Number: MSCU1234567\n"
    )
    scope = build_message_scope(body)
    assert scope.scope_type == "forward"
    assert "cancel this booking" in scope.classification_text
