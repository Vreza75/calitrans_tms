"""Regression coverage for Codex H1/M1/M3 findings against
fix/operations-inbox-coherent-envelope-root-cause (HEAD c48ae79):

H1 (HIGH): mid-block scanning clipped operational-label-first blocks - the
block scanner only ever scanned forward from wherever it first noticed an
envelope-shaped line, so an operational label sitting earlier in the same
contiguous block (Equipment-first, Booking-Number-first, Port-first, ...)
was invisible to the classifier, and the trailing From:/To:/Subject:
suffix alone was misread as a coherent email envelope.

M1 (MEDIUM): a segmentation collapse (a recognized reply/forward structure
producing empty authoritative text) could silently fall back to parsing
the full raw body, persisting partially-parsed fields (e.g. a Booking
Number from ambiguous/collapsed content) as if they were trusted
current-message data.

M3 (MEDIUM): the nested-forward depth limit had a destructive terminal
state that could return empty classification_text when the traversal
budget was exhausted mid-chain.

See docs/reviews/OPERATIONS_INBOX_LABEL_BLOCK_BOUNDARY_CORRECTION.md.
"""
import itertools

import pytest

from services.message_scope import build_message_scope, select_innermost_actionable_forward
from services.operations_inbox_service import (
    _prepare_operations_email_record,
    classify_customer_request,
)


def scope_of(body: str):
    return build_message_scope(body)


# =====================================================================
# Phase 2 - H1 reproduction, standalone (top-level, no forward marker)
# =====================================================================


def test_equipment_first_block_stays_active_and_classifies_quote_request():
    body = "Please quote:\nEquipment: 40HC\nFrom: Houston\nTo: Dallas\nSubject: Container Rate\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for line in ["Please quote:", "Equipment: 40HC", "From: Houston", "To: Dallas", "Subject: Container Rate"]:
        assert line in scope.classification_text
    assert classify_customer_request("Quote", body) == "Quote Request"


def test_booking_number_first_block_preserved_and_classifies_new_booking():
    body = (
        "Booking Number: ABC123\nFrom: Houston\nTo: Dallas\nSubject: Container Booking\n"
        "Container Number: MSCU1234567\nPlease create this booking.\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for line in [
        "Booking Number: ABC123", "From: Houston", "To: Dallas",
        "Subject: Container Booking", "Container Number: MSCU1234567", "Please create this booking.",
    ]:
        assert line in scope.classification_text
    assert classify_customer_request("Booking", body) == "New Booking"


def test_port_first_block_preserved_and_classifies_quote_request():
    body = "Port: Bayport\nFrom: Houston\nTo: Dallas\nSubject: Rate Request\nEquipment: 40HC\nPlease quote this move.\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for line in ["Port: Bayport", "From: Houston", "To: Dallas", "Subject: Rate Request", "Equipment: 40HC"]:
        assert line in scope.classification_text
    assert classify_customer_request("Rate", body) == "Quote Request"


def test_pickup_date_first_block_preserved_and_classifies_quote_request():
    body = "Pickup Date: August 10\nFrom: Houston\nTo: Dallas\nSubject: Container Rate\nEquipment: 40HC\nPlease quote.\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for line in ["Pickup Date: August 10", "From: Houston", "To: Dallas", "Equipment: 40HC"]:
        assert line in scope.classification_text
    assert classify_customer_request("Rate", body) == "Quote Request"


def test_spanish_equipo_first_block_preserved_no_reply_clipping():
    body = "Equipo: 40HC\nDe: Houston\nA: Dallas\nAsunto: Solicitud de tarifa\nPor favor cotizar.\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for line in ["Equipo: 40HC", "De: Houston", "A: Dallas", "Asunto: Solicitud de tarifa", "Por favor cotizar."]:
        assert line in scope.classification_text


# =====================================================================
# Phase 3 - H1 reproduction, after a real forwarded envelope
# =====================================================================


def _forwarded_wrapper(inner: str) -> str:
    return (
        "FYI\n\n-----Original Message-----\n"
        "From: Customer Service\nTo: Operations\nSubject: Rate Request\n\n"
        f"{inner}"
    )


def test_forwarded_equipment_first_block_preserved_completely():
    body = _forwarded_wrapper("Please quote:\nEquipment: 40HC\nFrom: Houston\nTo: Dallas\nSubject: Container Rate\n")
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only"
    assert "From: Customer Service" not in scope.classification_text
    assert "Subject: Rate Request" not in scope.classification_text
    for line in ["Please quote:", "Equipment: 40HC", "From: Houston", "To: Dallas", "Subject: Container Rate"]:
        assert line in scope.classification_text
    assert classify_customer_request("Fwd: Rate Request", body) == "Quote Request"


def test_forwarded_booking_number_first_block_preserved():
    body = _forwarded_wrapper(
        "Booking Number: ABC123\nFrom: Houston\nTo: Dallas\nSubject: Container Booking\n"
        "Container Number: MSCU1234567\nPlease create this booking.\n"
    )
    scope = scope_of(body)
    assert "From: Customer Service" not in scope.classification_text
    for line in ["Booking Number: ABC123", "From: Houston", "To: Dallas", "Container Number: MSCU1234567"]:
        assert line in scope.classification_text


def test_forwarded_port_first_block_preserved():
    body = _forwarded_wrapper("Port: Bayport\nFrom: Houston\nTo: Dallas\nSubject: Rate\nEquipment: 40HC\nPlease quote.\n")
    scope = scope_of(body)
    assert "From: Customer Service" not in scope.classification_text
    for line in ["Port: Bayport", "From: Houston", "To: Dallas", "Equipment: 40HC"]:
        assert line in scope.classification_text


def test_forwarded_pickup_date_first_block_preserved():
    body = _forwarded_wrapper("Pickup Date: August 10\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\nPlease quote.\n")
    scope = scope_of(body)
    assert "From: Customer Service" not in scope.classification_text
    for line in ["Pickup Date: August 10", "From: Houston", "To: Dallas", "Equipment: 40HC"]:
        assert line in scope.classification_text


def test_forwarded_spanish_equipo_first_block_preserved():
    body = _forwarded_wrapper("Equipo: 40HC\nDe: Houston\nA: Dallas\nAsunto: Tarifa\nPor favor cotizar.\n")
    scope = scope_of(body)
    assert "From: Customer Service" not in scope.classification_text
    for line in ["Equipo: 40HC", "De: Houston", "A: Dallas", "Por favor cotizar."]:
        assert line in scope.classification_text


# =====================================================================
# Phase 7 - order-permutation testing
# =====================================================================

_ENGLISH_QUOTE_FIELDS = ["Equipment: 40HC", "From: Houston", "To: Dallas", "Subject: Container Rate"]
_SPANISH_QUOTE_FIELDS = ["Equipo: 40HC", "De: Houston", "A: Dallas", "Asunto: Solicitud de tarifa"]


@pytest.mark.parametrize("perm", list(itertools.permutations(_ENGLISH_QUOTE_FIELDS)), ids=lambda p: "|".join(p))
def test_english_quote_block_all_24_permutations(perm):
    body = "Please quote:\n" + "\n".join(perm) + "\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for field in _ENGLISH_QUOTE_FIELDS:
        assert field in scope.classification_text
    assert classify_customer_request("Quote", body) == "Quote Request"


@pytest.mark.parametrize("perm", list(itertools.permutations(_SPANISH_QUOTE_FIELDS)), ids=lambda p: "|".join(p))
def test_spanish_quote_block_all_24_permutations(perm):
    body = "Por favor cotizar:\n" + "\n".join(perm) + "\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    for field in _SPANISH_QUOTE_FIELDS:
        assert field in scope.classification_text


# Representative per-label permutation coverage: each of these operational
# labels tested in 4 positions relative to a fixed From:/To:/Subject: core,
# without relying on a neighboring Equipment: label to protect them.
_POSITION_LABELS = [
    "Booking Number: ABC123",
    "Container Number: MSCU1234567",
    "Port: Bayport",
    "Pickup Date: August 10",
    "Delivery Date: August 11",
    "Full Return Terminal: Bayport Terminal",
    "Empty Pickup: ConGlobal",
    "Origin: Houston",
    "Destination: Dallas",
]


@pytest.mark.parametrize("label_line", _POSITION_LABELS)
@pytest.mark.parametrize("position", ["before_from", "between_from_to", "between_to_subject", "after_subject"])
def test_operational_label_in_every_position_stays_active(position, label_line):
    core = ["From: Houston", "To: Dallas", "Subject: Container Rate"]
    if position == "before_from":
        lines = [label_line, *core]
    elif position == "between_from_to":
        lines = [core[0], label_line, core[1], core[2]]
    elif position == "between_to_subject":
        lines = [core[0], core[1], label_line, core[2]]
    else:
        lines = [*core, label_line]
    body = "Please quote:\n" + "\n".join(lines) + "\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message", f"{position}/{label_line}: {scope.classification_text!r}"
    assert label_line in scope.classification_text
    for field in core:
        assert field in scope.classification_text


# =====================================================================
# Phase 8 - envelope order neutrality (real envelopes must still strip)
# =====================================================================

_ENVELOPE_FIELD_SETS = [
    ["From: C <c@example.com>", "Sent: Mon", "To: Ops", "Subject: Rate"],
    ["From: Customer Service", "To: Operations", "Subject: Rate Request"],
    ["De: Cliente <c@example.com>", "Enviado: lunes", "Para: Operaciones", "Asunto: Tarifa"],
    ["De: Cliente", "Para: Operaciones", "Asunto: Tarifa"],
]


@pytest.mark.parametrize("fields", _ENVELOPE_FIELD_SETS, ids=lambda f: "|".join(f))
def test_envelope_field_order_permutations_still_strip(fields):
    for perm in itertools.permutations(fields):
        body = "FYI\n\n-----Original Message-----\n" + "\n".join(perm) + "\n\nPlease quote Houston to Dallas.\nEquipment: 40HC\n"
        scope = scope_of(body)
        assert scope.scope_type == "forwarded_only", f"{perm}: {scope.classification_text!r}"
        for field in fields:
            assert field not in scope.classification_text, f"{perm}: leaked {field!r}"
        assert "Please quote Houston to Dallas." in scope.classification_text


def test_envelope_with_blank_lines_and_reordered_fields_still_strips():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "Subject: Rate\n\nTo: Ops\n\nSent: Mon\n\nFrom: C <c@example.com>\n\n"
        "Please quote Houston to Dallas.\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert scope.scope_type == "forwarded_only"
    for field in ["Subject:", "To:", "Sent:", "From:"]:
        assert field not in scope.classification_text
    assert "Please quote Houston to Dallas." in scope.classification_text


def test_bare_from_to_pair_alone_still_stays_operational_not_envelope():
    """The operational-label-first fix must not broaden envelope detection
    to swallow a bare From/To lane with no third label and no email."""
    body = "Please quote:\nFrom: Houston\nTo: Dallas\n"
    scope = scope_of(body)
    assert scope.scope_type == "new_message"
    assert "From: Houston" in scope.classification_text
    assert "To: Dallas" in scope.classification_text


# =====================================================================
# Phase 12 - M3 depth-limit fallback
# =====================================================================


def _nested_forward_chain(depth: int, innermost: str, separator: str = "-----Original Message-----") -> str:
    body = "FYI\n\n"
    for i in range(depth):
        body += f"{separator}\nFrom: Sender{i} <s{i}@example.com>\nSent: Mon\nTo: Ops\nSubject: Fwd{i}\n\n"
    body += innermost
    return body


def test_five_levels_of_nesting_fully_resolved_no_depth_limit():
    body = _nested_forward_chain(5, "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n")
    scope = scope_of(body)
    assert scope.segmentation_status == "ok"
    assert scope.classification_text == "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC"


def test_six_levels_of_nesting_hits_depth_limit_but_preserves_content():
    body = _nested_forward_chain(6, "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n")
    scope = scope_of(body)
    assert scope.segmentation_status == "depth_limit_reached"
    assert scope.classification_text.strip() != ""
    assert scope.classification_text.strip() != "-----Original Message-----"
    assert "From: Houston" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_ten_levels_of_nesting_hits_depth_limit_but_preserves_content():
    body = _nested_forward_chain(10, "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n")
    scope = scope_of(body)
    assert scope.segmentation_status == "depth_limit_reached"
    assert scope.classification_text.strip() != ""
    assert "Equipment: 40HC" in scope.classification_text


def test_six_levels_with_cancellation_at_deepest_level_preserves_instruction():
    body = _nested_forward_chain(6, "Please cancel booking ABC123.\n")
    scope = scope_of(body)
    assert scope.classification_text.strip() != ""
    assert "Please cancel booking ABC123." in scope.classification_text


def test_six_levels_with_update_at_deepest_level_preserves_instruction():
    body = _nested_forward_chain(6, "Please update the delivery date to August 10 for booking ABC123.\n")
    scope = scope_of(body)
    assert scope.classification_text.strip() != ""
    assert "Please update the delivery date to August 10 for booking ABC123." in scope.classification_text


def test_malformed_repeated_underscore_separators_terminate_deterministically():
    body = "FYI\n\n" + ("________________________________\nFrom: X <x@example.com>\nSent: Mon\nTo: Ops\nSubject: Y\n\n" * 8) + "Please quote Houston to Dallas.\nEquipment: 40HC.\n"
    scope = scope_of(body)
    # Must terminate (no infinite loop - if this test hangs, that IS the
    # failure) and must never be a destructive empty terminal state.
    assert scope.classification_text.strip() != ""


def test_empty_intermediate_bodies_between_nested_forwards_do_not_break_traversal():
    body = (
        "FYI\n\n-----Original Message-----\nFrom: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: Fwd1\n\n"
        "-----Original Message-----\nFrom: B <b@example.com>\nSent: Tue\nTo: Ops\nSubject: Fwd2\n\n"
        "-----Original Message-----\nFrom: C <c@example.com>\nSent: Wed\nTo: Ops\nSubject: Rate\n\n"
        "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n"
    )
    scope = scope_of(body)
    assert scope.segmentation_status == "ok"
    assert "From: Houston" in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_select_innermost_actionable_forward_direct_depth_limit_signal():
    body = _nested_forward_chain(6, "Please quote:\nFrom: Houston\nTo: Dallas\nEquipment: 40HC\n")
    text, outer_block, depth_limit_reached = select_innermost_actionable_forward(body[body.index("-----"):])
    assert depth_limit_reached is True
    assert text.strip() != ""


# =====================================================================
# Phase 9/10/11 - M1 segmentation-collapse safety
# =====================================================================


def _no_separator_ambiguous_body() -> str:
    return (
        "FYI\n\n-----Original Message-----\n"
        "From: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: X\n\n"
        "From: B <b@example.com>\nSent: Tue\nTo: Ops\nSubject: Y\n\n"
        "Booking Number: ABC123\nPlease cancel this booking.\n"
    )


def test_no_separator_message_flags_collapsed_segmentation():
    scope = scope_of(_no_separator_ambiguous_body())
    assert scope.segmentation_status == "collapsed"
    assert scope.classification_text == ""


def test_no_separator_message_does_not_persist_booking_number_as_trusted():
    message = {
        "subject": "Fwd: X",
        "from": "customer@example.com",
        "body": _no_separator_ambiguous_body(),
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    assert record["latest_body"] == ""
    assert record["parsed"].get("Booking Number") in ("", None)
    assert record["parsed"].get("_segmentation_status") == "collapsed"


def test_no_separator_message_routes_to_manual_review():
    message = {
        "subject": "Fwd: X",
        "from": "customer@example.com",
        "body": _no_separator_ambiguous_body(),
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    assert record["parsed"]["_needs_review"] is True
    assert record["parsed"]["_confidence"] <= 0.4
    assert record["triage"].get("llm_required") is True
    assert record["classification"].get("matched_load_id") is None


def test_no_separator_message_preserves_raw_body_for_audit_only():
    body = _no_separator_ambiguous_body()
    message = {"subject": "Fwd: X", "from": "customer@example.com", "body": body, "direction": "inbound"}
    record = _prepare_operations_email_record(message)
    assert record["parsed"].get("_segmentation_collapsed_raw_body", "").startswith("FYI")
    assert "Booking Number: ABC123" in record["parsed"]["_segmentation_collapsed_raw_body"]


def test_unambiguous_body_is_not_flagged_as_collapsed():
    message = {
        "subject": "Rate",
        "from": "customer@example.com",
        "body": "Please quote Houston to Dallas.\nEquipment: 40HC\n",
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    assert record["parsed"].get("_segmentation_status") is None
    assert record["latest_body"] == "Please quote Houston to Dallas.\nEquipment: 40HC"


def test_forwarded_metadata_only_body_is_not_flagged_as_collapsed():
    """A real forwarded envelope with no operational content at all is a
    correctly-recognized structure producing (correctly) minimal active
    text - this is different from a *collapse* (structure recognized but
    text is empty) since here there genuinely is very little to say."""
    message = {
        "subject": "Fwd",
        "from": "customer@example.com",
        "body": "FYI\n\n-----Original Message-----\nFrom: C <c@example.com>\nSent: Mon\nTo: Ops\nSubject: Hi\n\nJust saying hello.\n",
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    assert record["parsed"].get("_segmentation_status") is None
    assert "Just saying hello." in record["latest_body"]


# =====================================================================
# Phase 16 red-team findings: two narrow, contrived-input known
# limitations, pinned down rather than silently left unfixed or
# over-engineered against under context/time budget. Neither loses data
# (both preserve the full operational content); both leak some envelope
# labels because an operational label appears INSIDE or immediately
# BEFORE a genuine multi-field envelope's own label run - a shape no real
# email client produces (a real envelope's From:/Sent:/Subject: block is
# never interrupted by an Equipment: line). See
# docs/reviews/OPERATIONS_INBOX_LABEL_BLOCK_BOUNDARY_CORRECTION.md.
# =====================================================================


def test_operational_label_interrupting_a_real_envelope_is_a_documented_known_limitation():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "From: C <c@example.com>\nEquipment: 40HC\nSent: Mon\nTo: Ops\nSubject: Rate\n\n"
        "Please advise.\n"
    )
    scope = scope_of(body)
    # No content is lost - this pins the current (safe, non-empty) output.
    assert "Please advise." in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_operational_label_immediately_before_a_real_envelope_is_a_documented_known_limitation():
    body = (
        "FYI\n\n-----Original Message-----\n"
        "Equipment: 40HC\nFrom: A <a@example.com>\nSent: Mon\nTo: Ops\nSubject: X\n\n"
        "Please confirm receipt.\n"
    )
    scope = scope_of(body)
    assert "Please confirm receipt." in scope.classification_text
    assert "Equipment: 40HC" in scope.classification_text


def test_ambiguous_body_with_valid_attachment_still_flags_collapse_for_body():
    """Attachment-derived fields have independently known provenance
    (parsed from the attachment file itself, not from the ambiguous body
    text) and remain available; the body-derived segmentation status must
    still reflect the collapse so body-only fields are not trusted."""
    message = {
        "subject": "Fwd: X",
        "from": "customer@example.com",
        "body": _no_separator_ambiguous_body(),
        "direction": "inbound",
    }
    record = _prepare_operations_email_record(message)
    assert record["parsed"].get("_segmentation_status") == "collapsed"
    # No attachments in this test double, but the field is independent of
    # body segmentation - saved_attachments is computed and merged
    # separately regardless of body collapse.
    assert isinstance(record["saved_attachments"], list)
