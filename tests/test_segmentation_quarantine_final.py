"""Regression coverage for Codex H1(blank-line)/H2/M1/L1 findings against
fix/operations-inbox-label-block-boundary-correction (HEAD 69b22f17):

H1 (blank-line variant, HIGH): an active operational block could absorb a
later genuine reply envelope across a blank line - the blank-line bridge
rule only ever checked whether the next label-shaped run STARTED with an
envelope-shaped label, never whether it was itself a coherent, independent
envelope. See services/message_scope.py::_scan_label_block /
_find_block_start.

H2 (HIGH): a segmentation-collapse's audit-only raw body
(_segmentation_collapsed_raw_body) could re-enter classification/token
extraction/matching through str(parsed)/f"...{parsed}" blobs built at
~10 call sites across operations_inbox_service.py,
operations_email_triage_service.py, and operations_case_service.py - and,
independently, a field's own KEY NAME (e.g. "Booking Number") could
satisfy its own extraction regex even with a blank value, producing a
synthetic "NUMBER" token. See services/operations_email_triage_service.py
::sanitize_parsed_for_classification / flatten_parsed_values_for_scan and
services/operations_inbox_service.py::coerce_parsed_for_classification /
operations_parsed_for_row.

M1 (MEDIUM): the quote-lane detector paired a From: value and a To: value
found anywhere in the scoped text, even across unrelated sections or
inside a genuine coherent email envelope. See services/message_scope.py
::non_envelope_label_blocks and operations_inbox_service.py::
_has_plausible_quote_lane.

L1 (LOW): the attachment-plus-collapse safety test supplied no attachment
at all. Corrected in tests/test_label_block_boundary_correction.py.

See docs/reviews/OPERATIONS_INBOX_SEGMENTATION_QUARANTINE_FINAL.md.
"""
import pytest

from services.message_scope import build_message_scope, non_envelope_label_blocks
from services.operations_email_triage_service import (
    flatten_parsed_values_for_scan,
    sanitize_parsed_for_classification,
    triage_operations_email,
)
from services.operations_inbox_service import (
    _has_plausible_quote_lane,
    _prepare_operations_email_record,
)


def scope_of(body: str):
    return build_message_scope(body)


# =====================================================================
# Phase 2/4 - H1 blank-line variants
# =====================================================================


def test_operational_block_then_blank_then_reply_envelope_stays_active():
    body = (
        "Equipment: 40HC\n\n"
        "From: Old Customer\nSent: Monday\nTo: Operations\nSubject: Cancellation\n\n"
        "Please cancel booking ABC123.\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "reply"
    assert scope.classification_text == "Equipment: 40HC"
    assert "From: Old Customer" not in scope.classification_text
    assert "Please cancel booking ABC123" not in scope.classification_text


def test_current_booking_number_survives_old_quote_across_blank():
    body = (
        "Booking Number: CURRENT123\n\n"
        "From: Old Customer\nSent: Monday\nTo: Operations\nSubject: Rate Request\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Booking Number: CURRENT123"


def test_origin_destination_survive_old_booking_across_blank():
    body = (
        "Origin: Houston\nDestination: Dallas\n\n"
        "From: Old Customer\nSent: Monday\nTo: Operations\nSubject: New Booking\n\n"
        "Booking Number: OLD123\nContainer Number: MSCU1234567\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Origin: Houston\nDestination: Dallas"
    assert "OLD123" not in scope.classification_text


def test_spanish_operational_survives_old_cancellation_across_blank():
    body = (
        "Equipo: 40HC\n\n"
        "De: Cliente Anterior\nEnviado: lunes\nPara: Operaciones\nAsunto: Cancelación\n\n"
        "Por favor cancelar reserva ABC123.\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Equipo: 40HC"


def test_no_sent_envelope_after_blank_is_still_treated_separately():
    body = (
        "Equipment: 40HC\n\n"
        "From: Old Customer\nTo: Operations\nSubject: Cancellation\n\n"
        "Please cancel booking ABC123.\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Equipment: 40HC"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_blank_line_split_is_stable_across_line_endings(newline):
    body = newline.join(
        [
            "Equipment: 40HC",
            "",
            "From: Old Customer",
            "Sent: Monday",
            "To: Operations",
            "Subject: Cancellation",
            "",
            "Please cancel booking ABC123.",
            "",
        ]
    )
    scope = scope_of(body)
    assert scope.classification_text == "Equipment: 40HC"


def test_two_blank_lines_and_whitespace_only_line_still_split():
    body = (
        "Equipment: 40HC\n \n\t\n\n"
        "From: Old Customer\nSent: Monday\nTo: Operations\nSubject: Cancellation\n\n"
        "Please cancel booking ABC123.\n"
    )
    scope = scope_of(body)
    assert scope.classification_text == "Equipment: 40HC"


def test_coherent_envelope_with_internal_blank_line_is_unaffected():
    """A blank line WITHIN a still-forming envelope (no operational
    evidence yet) must keep bridging - only an already-operational block
    is protected from bridging into a later coherent envelope."""
    body = "From: Customer\n\nSent: Monday\nTo: Operations\nSubject: Rate Request\n"
    scope = scope_of(body)
    assert scope.scope_type == "reply"
    assert scope.classification_text == ""


def test_two_operational_sections_separated_by_blank_both_stay_active():
    body = "Equipment: 40HC\n\nPickup Date: August 10\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    assert "Equipment: 40HC" in scope.classification_text
    assert "Pickup Date: August 10" in scope.classification_text


def test_from_to_pair_then_blank_then_equipment_all_stay_active():
    body = "From: Houston\nTo: Dallas\n\nEquipment: 40HC\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    assert scope.classification_text == body.strip()


# =====================================================================
# Phase 5/10 - H2 quarantine, exact Codex fixture, full production path
# =====================================================================


def _codex_no_separator_fixture() -> str:
    return (
        "FYI\n\n-----Original Message-----\n"
        "From: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: X\n\n"
        "From: B <b@example.com>\nSent: Tue\nTo: Ops\nSubject: Y\n\n"
        "Booking Number: ABC123\nPlease cancel this booking.\n"
    )


def test_h2_collapsed_fixture_end_to_end_routes_neutral_and_conservative():
    message = {
        "subject": "Fwd: X",
        "from": "customer@example.com",
        "body": _codex_no_separator_fixture(),
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    triage = record["triage"]
    classification = record["classification"]

    assert record["parsed"].get("_segmentation_status") == "collapsed"
    assert record["latest_body"] == ""

    # No synthetic/audit-derived tokens.
    assert triage.get("tokens") == {"booking_number": "", "container_number": "", "reference_number": ""}
    assert classification.get("tokens") == {"booking_number": "", "container_number": "", "reference_number": ""}

    # Neutral, conservative, non-automatic routing (Invariant 4).
    assert triage.get("request_type") == "Customer Request"
    assert int(triage.get("confidence_score") or 0) <= 40
    assert triage.get("work_queue") == "Review"
    assert triage.get("should_open_case") is False
    assert triage.get("llm_required") is True
    assert classification.get("matched_load_id") is None
    assert record["parsed"].get("Booking Number") in ("", None)


def test_booking_number_key_alone_never_synthesizes_number_token():
    """A field present with an empty value must never let its own KEY NAME
    satisfy the regex meant to extract a value FROM free text."""
    parsed = {"Booking Number": "", "Container Number": "", "Reference Number": ""}
    tokens = flatten_parsed_values_for_scan(parsed)
    assert tokens == ""


def test_sanitize_strips_underscore_keys_but_keeps_trusted_fields():
    parsed = {
        "Booking Number": "REAL123",
        "_segmentation_status": "collapsed",
        "_segmentation_collapsed_raw_body": "Booking Number: HIDDEN999\ncancel this",
        "_fast_triage": {"request_type": "Cancellation"},
    }
    sanitized = sanitize_parsed_for_classification(parsed)
    assert sanitized == {"Booking Number": "REAL123"}
    blob = flatten_parsed_values_for_scan(parsed)
    assert "HIDDEN999" not in blob
    assert "collapsed" not in blob
    assert "REAL123" in blob


def test_collapsed_segmentation_status_label_itself_is_not_a_token_source():
    parsed = {"_segmentation_status": "collapsed", "_segmentation_collapsed_raw_body": "Container Number: HIDDEN9999999"}
    blob = flatten_parsed_values_for_scan(parsed)
    assert blob == ""


def test_nested_dict_and_list_values_are_flattened_without_key_leakage():
    parsed = {
        "Notes": {"inner": "Booking Number: NESTED123"},
        "Container Numbers": ["MSCU1111111", "MSCU2222222"],
        "_audit_nested": {"raw": "should never appear"},
    }
    blob = flatten_parsed_values_for_scan(parsed)
    assert "NESTED123" in blob
    assert "MSCU1111111" in blob and "MSCU2222222" in blob
    assert "should never appear" not in blob


def test_triage_operations_email_text_blob_excludes_audit_values():
    parsed = {
        "_segmentation_status": "collapsed",
        "_segmentation_collapsed_raw_body": "please cancel this booking urgently",
    }
    result = triage_operations_email(subject="Fwd: X", body="", parsed=parsed)
    # Fast-rules keyword text must never see the audit-only cancellation
    # language, so nothing should push this toward "Cancellation".
    assert result["request_type"] != "Cancellation"


# =====================================================================
# Phase 12 - M1 block-bounded lane detection
# =====================================================================


def test_from_and_to_in_unrelated_sections_are_not_a_lane():
    text = "Please quote this.\nFrom: Houston\nEquipment: 40HC\n\nUnrelated recipient section:\nTo: Dallas\n"
    assert _has_plausible_quote_lane(text) is False


def test_from_and_to_in_same_operational_block_is_a_lane():
    text = "From: Houston\nEquipment: 40HC\nTo: Dallas\n"
    assert _has_plausible_quote_lane(text) is True


def test_from_to_separated_by_one_blank_still_pairs():
    text = "From: Houston\n\nTo: Dallas\n"
    assert _has_plausible_quote_lane(text) is True


def test_from_to_separated_by_prose_between_blanks_does_not_pair():
    text = "From: Houston\n\nUnrelated instructions here.\n\nTo: Dallas\n"
    assert _has_plausible_quote_lane(text) is False


def test_coherent_envelope_from_to_subject_is_never_a_lane():
    text = "From: Customer Service\nTo: Operations\nSubject: Rate Request\n"
    assert _has_plausible_quote_lane(text) is False


def test_coherent_envelope_with_plausible_place_like_names_is_never_a_lane():
    """Regression guard: a genuine 3-label envelope must be excluded by
    BLOCK KIND, not merely happen to fail the word-plausibility filter."""
    text = "From: John Smith\nTo: Jane Doe\nSubject: Rate Request\n"
    assert _has_plausible_quote_lane(text) is False


def test_two_separate_order_blocks_each_holding_half_a_pair_do_not_cross_pair():
    text = "Order 1\nFrom: Houston\nEquipment: 40HC\n\nOrder 2\nTo: Dallas\nEquipment: 20GP\n"
    assert _has_plausible_quote_lane(text) is False


def test_current_from_with_quoted_history_to_does_not_pair():
    text = "From: Houston\nEquipment: 40HC\n\nOn Monday, Bob wrote:\n> To: Dallas\n> old stuff\n"
    assert _has_plausible_quote_lane(text) is False


def test_non_envelope_label_blocks_excludes_coherent_envelope():
    text = "From: Customer Service\nTo: Operations\nSubject: Rate Request\n"
    assert non_envelope_label_blocks(text) == ()


def test_non_envelope_label_blocks_keeps_operational_block():
    text = "From: Houston\nEquipment: 40HC\nTo: Dallas\n"
    blocks = non_envelope_label_blocks(text)
    assert len(blocks) == 1
    assert "Equipment: 40HC" in blocks[0]


# =====================================================================
# Phase 15/16 - red-team boundary/quarantine probes (post-fix)
# =====================================================================


@pytest.mark.parametrize(
    "body",
    [
        "Equipment: 40HC\n\nOn Mon, Jan 5, 2026, Old Customer wrote:\nPlease cancel booking ABC123.\n",
        "Equipment: 40HC\n\nEl lunes, Cliente escribió:\nPor favor cancelar reserva ABC123.\n",
        "Thanks for the update.\n\nFrom: Old Customer\nSent: Monday\nTo: Operations\nSubject: Cancellation\n\nPlease cancel booking ABC123.\n",
        "Best regards,\nJohn\n\nFrom: Old Customer\nSent: Monday\nTo: Operations\nSubject: Cancellation\n\nPlease cancel booking ABC123.\n",
    ],
)
def test_red_team_blank_boundary_probes_preserve_active_content(body):
    scope = scope_of(body)
    # None of these probes lose the message's own leading active content
    # to the trailing reply/quote history.
    assert "Please cancel booking ABC123" not in scope.classification_text
    assert "cancelar reserva ABC123" not in scope.classification_text


@pytest.mark.parametrize(
    "parsed",
    [
        {"_parser_failures": ["Booking Number: LOOKS_REAL123 failed validation"]},
        {"_review_reasons": {"note": "Container Number: SNEAKY9999999"}},
        {"_operations_attachments": [{"filename": "Booking Number: FILE123.pdf"}]},
        {"_email_sync_errors": ["cancel booking ABC123 - parse failed"]},
        {"_candidate_conflicts": ["Booking Number"]},
    ],
)
def test_red_team_quarantine_probes_never_leak_into_scan_blob(parsed):
    blob = flatten_parsed_values_for_scan(parsed)
    assert blob == "" or "ABC123" not in blob and "SNEAKY" not in blob and "LOOKS_REAL" not in blob
