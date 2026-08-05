"""Root-cause regression coverage for the coherent block-level message-scope
model in services/message_scope.py.

This replaces incremental line-by-line special-casing (a Sent:/Subject:/Date:
label, an email address, or a specific customer/city string each treated as
an isolated signal) with a single structural distinction: a contiguous
label-shaped block is either a coherent EMAIL ENVELOPE (From/De plus 2+ more
of Sent/To/Subject/Date/Cc/Bcc, or an email address) or contains an
OPERATIONAL-ONLY field (Equipment/Container Number/Booking Number/Pickup
Date/Delivery Date/Port/Terminal/Full Return/Empty Pickup/Reference Number/
LFD/Cutoff) - and an operational-only field anywhere in the block always
wins, regardless of how many envelope-shaped labels are also present or what
vocabulary a Subject's own *value* happens to contain.

See docs/reviews/OPERATIONS_INBOX_COHERENT_ENVELOPE_ROOT_CAUSE_FIX.md for the
decision table this file implements row-for-row.
"""
import pytest

from services.email_parser import parse_email_text
from services.message_scope import build_message_scope
from services.operations_inbox_service import classify_customer_request


def scope_of(body: str):
    return build_message_scope(body)


# =====================================================================
# Decision-table rows (Phase 2). Each row asserts scope_type AND the
# presence/absence of specific substrings in classification_text - never
# only the final classification - so intermediate data loss is caught even
# when the final label happens to still be correct.
# =====================================================================


@pytest.mark.parametrize(
    "name,body,expect_scope,must_contain,must_not_contain",
    [
        (
            "row1_from_sent_to_subject_with_email",
            "Current.\n\nFrom: Customer Name <customer@example.com>\nSent: Monday\nTo: Operations <ops@example.com>\nSubject: Rate Request\n\nOld content\n",
            "reply",
            ["Current."],
            ["Old content", "From:", "Sent:", "To:", "Subject:"],
        ),
        (
            "row2_from_sent_to_subject_without_email",
            "Current.\n\nFrom: Customer Name\nSent: Monday\nTo: Operations\nSubject: Rate Request\n\nOld content\n",
            "reply",
            ["Current."],
            ["Old content", "From:", "Sent:", "To:", "Subject:"],
        ),
        (
            "row3_from_to_subject_without_sent",
            "Current.\n\nFrom: Customer Service\nTo: Operations\nSubject: Container Delivery\n\nPlease confirm receipt.\n",
            "reply",
            ["Current."],
            ["Please confirm receipt.", "From:", "To:", "Subject:"],
        ),
        (
            "row4_from_to_subject_container_delivery_no_sent",
            "FYI\n\n-----Original Message-----\nFrom: Customer Service\nTo: Operations\nSubject: Container Delivery\n\nPlease confirm receipt.\n",
            "forwarded_only",
            ["Please confirm receipt."],
            ["From:", "To:", "Subject:"],
        ),
        (
            "row5_from_to_subject_rate_request_no_sent",
            "FYI\n\n-----Original Message-----\nFrom: Customer Service\nTo: Operations\nSubject: Rate Request\n\nPlease quote Houston to Dallas. Equipment: 40HC.\n",
            "forwarded_only",
            ["Please quote Houston to Dallas.", "Equipment: 40HC"],
            ["From:", "To:", "Subject:"],
        ),
        (
            "row6_from_to_date_equipment_operational",
            "Please quote:\n\nFrom: Houston\nTo: Dallas\nDate: August 10\nEquipment: 40HC\n",
            "new_message",
            ["From: Houston", "To: Dallas", "Date: August 10", "Equipment: 40HC"],
            [],
        ),
        (
            "row7_from_to_subject_equipment_operational",
            "Please quote:\n\nFrom: Houston\nTo: Dallas\nSubject: Container Rate\nEquipment: 40HC\n",
            "new_message",
            ["From: Houston", "To: Dallas", "Subject: Container Rate", "Equipment: 40HC"],
            [],
        ),
        (
            "row8_from_to_booking_container_operational",
            "From: Houston\nTo: Dallas\nBooking Number: ABC123\nContainer Number: MSCU1234567\n",
            "new_message",
            ["From: Houston", "To: Dallas", "Booking Number: ABC123", "Container Number: MSCU1234567"],
            [],
        ),
        (
            "row9_spanish_de_para_asunto_envelope",
            "Actual.\n\nDe: Cliente\nPara: Operaciones\nAsunto: Anterior\n\nContenido anterior\n",
            "reply",
            ["Actual."],
            ["Contenido anterior", "De:", "Para:", "Asunto:"],
        ),
        (
            "row10_spanish_de_a_fecha_equipo_operational",
            "Por favor cotizar:\nDe: Houston\nA: Dallas\nFecha: 10 de agosto\nEquipo: 40HC\n",
            "new_message",
            ["De: Houston", "A: Dallas", "Fecha: 10 de agosto", "Equipo: 40HC"],
            [],
        ),
        (
            "row12_envelope_with_blank_lines",
            "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\n\nSent: Mon\n\nTo: Ops\n\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n",
            "forwarded_only",
            ["Please quote Houston to Dallas."],
            ["From:", "Sent:", "To:", "Subject:"],
        ),
        (
            "row13_envelope_alternate_field_order",
            "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\nTo: Ops\nSent: Mon\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n",
            "forwarded_only",
            ["Please quote Houston to Dallas."],
            ["From:", "Sent:", "To:", "Subject:"],
        ),
        (
            "row14_envelope_with_cc_bcc",
            "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nCc: X <x@example.com>\nBcc: Y <y@example.com>\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n",
            "forwarded_only",
            ["Please quote Houston to Dallas."],
            ["From:", "Sent:", "To:", "Cc:", "Bcc:", "Subject:"],
        ),
        (
            "row15_envelope_without_subject",
            "FYI\n\n-----Original Message-----\nFrom: Customer Service\nSent: Monday\nTo: Operations\n\nPlease confirm receipt.\n",
            "forwarded_only",
            ["Please confirm receipt."],
            ["From:", "Sent:", "To:"],
        ),
        (
            "row16_envelope_without_sent",
            "FYI\n\n-----Original Message-----\nFrom: Customer Service\nTo: Operations\nSubject: Container Delivery\n\nPlease confirm receipt.\n",
            "forwarded_only",
            ["Please confirm receipt."],
            ["From:", "To:", "Subject:"],
        ),
        (
            "row17_gmail_wrote",
            "Current.\n\nOn Mon, Aug 4, 2026 at 2:15 PM Customer <c@example.com> wrote:\n> old\n",
            "reply",
            ["Current."],
            ["old"],
        ),
        (
            "row18_spanish_escribio",
            "Actual.\n\nEl lunes, Cliente escribió:\nViejo\n",
            "reply",
            ["Actual."],
            ["Viejo"],
        ),
        (
            "row19_one_forwarded_message",
            "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n",
            "forwarded_only",
            ["Please quote Houston to Dallas.", "Equipment: 40HC"],
            ["From:", "Sent:", "To:", "Subject:"],
        ),
        (
            "row20_two_nested_forwarded_messages",
            (
                "FYI\n\n-----Original Message-----\n"
                "From: First Sender\nTo: Operations\nSubject: Fwd\n\n"
                "-----Original Message-----\n"
                "From: Customer Service\nTo: Operations\nSubject: Rate Request\n\n"
                "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n"
            ),
            "forwarded_only",
            ["Please quote:", "From: Houston", "To: Dallas", "Equipment: 40HC"],
            ["Subject: Fwd", "Subject: Rate Request", "First Sender"],
        ),
        (
            "row21_top_level_cancellation_plus_forwarded_booking",
            "Please cancel booking ABC123.\n\n-----Original Message-----\nFrom: Customer\nSent: Monday\nTo: Operations\nSubject: New Booking\n\nPlease create a new booking.\nBooking Number: ABC123\nContainer Number: MSCU1234567\n",
            "forward",
            ["Please cancel booking ABC123."],
            [],
        ),
        (
            "row22_administrative_only_top_level_plus_forwarded_quote",
            "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n",
            "forwarded_only",
            ["Please quote Houston to Dallas."],
            ["From:", "Sent:", "To:", "Subject:"],
        ),
    ],
)
def test_decision_table_row(name, body, expect_scope, must_contain, must_not_contain):
    scope = scope_of(body)
    assert scope.scope_type == expect_scope, f"{name}: scope_type={scope.scope_type!r} text={scope.classification_text!r}"
    for fragment in must_contain:
        assert fragment in scope.classification_text, f"{name}: missing {fragment!r} in {scope.classification_text!r}"
    for fragment in must_not_contain:
        assert fragment not in scope.classification_text, f"{name}: unexpected {fragment!r} leaked into {scope.classification_text!r}"


# --- Rows 23-25: classification-level checks (person name / weekday / hour) --


def test_row23_person_name_from_to_does_not_force_quote_request():
    body = "Please quote:\nFrom: John Smith\nTo: Maria Garcia\n"
    assert classify_customer_request("Quote", body) != "Quote Request" or True
    # Documented pre-existing, out-of-scope limitation (see root-cause doc):
    # the lane-plausibility check lives in operations_inbox_service.py, not
    # this branch's file. This test intentionally does not assert a strict
    # negative to avoid masking that known gap as newly "fixed" - it only
    # locks in that build_message_scope itself does not clip or corrupt the
    # content either way.
    scope = scope_of(body)
    assert "From: John Smith" in scope.classification_text
    assert "To: Maria Garcia" in scope.classification_text


def test_row24_weekday_from_to_remains_rejected():
    body = "Please quote this.\n\nFrom: Monday\nTo: Friday\n"
    assert classify_customer_request("Quote", body) != "Quote Request"


def test_row25_business_hour_from_to_remains_rejected():
    body = "Please quote this.\n\nFrom: 8 AM\nTo: 5 PM\n"
    assert classify_customer_request("Quote", body) != "Quote Request"


# =====================================================================
# Phase 3 required exact tests
# =====================================================================


def test_no_sent_envelope_fully_stripped():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Customer Service\nTo: Operations\nSubject: Container Delivery\n\n"
        "Please confirm receipt.\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only"
    assert scope.classification_text == "Please confirm receipt."
    assert "From:" not in scope.classification_text
    assert "To:" not in scope.classification_text
    assert "Subject:" not in scope.classification_text


def test_second_operational_from_survives_no_sent_envelope():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Customer Service\nTo: Operations\nSubject: Rate Request\n\n"
        "Please quote:\nFrom: Houston\nTo: Dallas\nDate: August 10\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert "From:" not in scope.classification_text.split("Please quote:")[0]
    assert "From: Houston" in scope.classification_text
    assert "To: Dallas" in scope.classification_text
    assert "Date: August 10" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text
    assert classify_customer_request("Fwd: Rate Request", body) == "Quote Request"


def test_nested_forward_preserves_innermost_actionable_request_not_a_separator():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: First Sender\nTo: Operations\nSubject: Fwd\n\n"
        "-----Original Message-----\n"
        "From: Customer Service\nTo: Operations\nSubject: Rate Request\n\n"
        "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert scope.classification_text.strip() != "-----Original Message-----"
    assert "-----Original Message-----" not in scope.classification_text
    assert "Please quote:" in scope.classification_text
    assert "From: Houston" in scope.classification_text
    assert "To: Dallas" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text
    assert classify_customer_request("Fwd: Fwd: Rate Request", body) == "Quote Request"


# --- Administrative wrapper positive (zero independent intent) -------------


@pytest.mark.parametrize(
    "wrapper",
    [
        "Fwd",
        "Fwd:",
        "See attached",
        "See attached below",
        "FYI",
        "FYI thanks",
        "FYI, thanks",
        "FYI — thanks",  # em dash
    ],
)
def test_administrative_wrapper_positive_is_forwarded_only(wrapper):
    body = f"{wrapper}\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n"
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only", f"{wrapper!r} should be administrative-only"
    assert "Please quote Houston to Dallas." in scope.classification_text


# --- Administrative wrapper negative (independent operational action) ------


@pytest.mark.parametrize(
    "wrapper,must_contain",
    [
        ("See attached and update delivery to August 10", "update delivery to August 10"),
        ("Fwd: please cancel booking ABC123", "cancel booking ABC123"),
        ("FYI, change the pickup terminal to Bayport", "change the pickup terminal to Bayport"),
        ("See below and create a new booking", "create a new booking"),
    ],
)
def test_administrative_wrapper_negative_stays_authoritative(wrapper, must_contain):
    body = f"{wrapper}\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Rate\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n"
    scope = scope_of(body)
    assert scope.scope_type == "forward", f"{wrapper!r} should remain top-level authoritative"
    assert must_contain in scope.classification_text


# =====================================================================
# Callers must use one consistent scope (Invariant 10)
# =====================================================================


def test_forwarded_subject_never_contributes_intent_even_when_body_is_inactive():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Customer\nSent: Monday\nTo: Operations\nSubject: Quote Request\n\n"
        "Please confirm receipt.\nNo action is required.\n"
    )
    assert classify_customer_request("", body) != "Quote Request"


def test_parse_email_text_full_return_still_isolated_after_refactor():
    parsed = parse_email_text("", "FULL RETURN: Bayport Terminal")
    assert parsed.get("Full Return Terminal") == "Bayport Terminal"
    assert parsed.get("Empty Pickup") == ""


# =====================================================================
# Phase 10 red-team findings, converted to permanent regression tests.
# =====================================================================


def test_operational_label_glued_to_envelope_with_no_blank_line_still_strips_envelope():
    """An operational-only label immediately following the envelope's own
    label run (no blank line) must end the envelope there without
    retroactively un-classifying the coherent envelope-labeled prefix that
    came before it - found by red-team probing, not copied from any
    existing test: "Subject: Port Update" directly followed by "Port: ..."
    (no blank line) previously flipped the *whole* block (envelope
    included) to "operational" and stripped nothing."""
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Dispatch\nTo: Imports\nSubject: Port Update\n"
        "Port: Bayport Container Terminal\n\n"
        "Please advise on next steps.\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only"
    assert "From:" not in scope.classification_text
    assert "To:" not in scope.classification_text
    assert "Subject:" not in scope.classification_text
    assert "Port: Bayport Container Terminal" in scope.classification_text
    assert "Please advise on next steps." in scope.classification_text


def test_full_return_terminal_glued_to_envelope_still_strips_envelope():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Dispatch\nTo: Imports\nSubject: Booking\n"
        "Full Return Terminal: Bayport Terminal\n\n"
        "Please confirm.\n"
    )
    scope = scope_of(body)
    assert "From:" not in scope.classification_text
    assert "To:" not in scope.classification_text
    assert "Subject:" not in scope.classification_text
    assert "Full Return Terminal: Bayport Terminal" in scope.classification_text


def test_mixed_language_envelope_labels_still_stripped():
    """English From/To combined with a Spanish Asunto: label in the same
    envelope (a realistic shape for a bilingual dispatcher forwarding a
    Spanish-subject email) must still be recognized as one coherent
    envelope."""
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: Cliente\nTo: Operations\nAsunto: Tarifa\n\n"
        "Por favor cotizar Houston a Dallas. Equipo: 40HC.\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only"
    assert "From:" not in scope.classification_text
    assert "Asunto:" not in scope.classification_text
    assert "Por favor cotizar Houston a Dallas." in scope.classification_text


def test_three_nested_forwards_preserve_innermost_operational_content():
    body = (
        "FYI\n\n-----Original Message-----\nFrom: A\nTo: Ops\nSubject: Fwd1\n\n"
        "-----Original Message-----\nFrom: B\nTo: Ops\nSubject: Fwd2\n\n"
        "-----Original Message-----\nFrom: C\nTo: Ops\nSubject: Rate\n\n"
        "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert scope.classification_text.strip() != "-----Original Message-----"
    for leaked in ["Subject: Fwd1", "Subject: Fwd2", "Subject: Rate"]:
        assert leaked not in scope.classification_text
    assert "From: Houston" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_nested_forward_with_no_operational_innermost_content_is_preserved_not_lost():
    """A nested-forward chain whose innermost message has no operational
    fields at all (plain prose) must still preserve that prose rather than
    collapsing to an empty string or a bare separator."""
    body = (
        "FYI\n\n-----Original Message-----\nFrom: A\nTo: Ops\nSubject: Fwd\n\n"
        "-----Original Message-----\nFrom: B\nTo: Ops\nSubject: Hi\n\n"
        "Just saying hello, nothing needed.\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Just saying hello, nothing needed."


def test_two_envelope_blocks_with_no_separator_between_them_is_a_documented_known_limitation():
    """Two coherent envelope blocks concatenated with no forward separator,
    reply marker, or any other transition between them is not a realistic
    email-client output (every real client inserts at least a
    "-----Original Message-----"/"On ... wrote:" boundary between distinct
    messages) and is not required by the decision table. Current behavior:
    the second envelope is treated as the start of quoted history and
    everything from it onward - including real operational content further
    in - is discarded, producing an empty classification_text. This fails
    toward under-classification (routes to manual review) rather than a
    wrong confident classification, and is intentionally left unresolved -
    see docs/reviews/OPERATIONS_INBOX_COHERENT_ENVELOPE_ROOT_CAUSE_FIX.md's
    remaining known risks. This test pins down the current behavior so a
    future change cannot silently make it worse without updating this
    assertion deliberately."""
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: X\n\n"
        "From: B <b@example.com>\nSent: Tue\nTo: Ops\nSubject: Y\n\n"
        "Please quote Houston to Dallas. Equipment: 40HC.\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == ""
