"""Regression coverage for services/operations_field_service.py's shared
candidate-generation/validation pipeline, covering defects found while
certifying the Operations Inbox intake pipeline against a disposable
Postgres database (see docs/reviews/OPERATIONS_INBOX_CERTIFICATION_INVESTIGATION.md).
"""
from services.operations_field_service import (
    generate_field_candidates,
    select_field_candidates,
    validate_field_value,
)


# --- Contact Name must not accept an operational "Label: Value" line ------


def test_contact_name_rejects_a_steamship_line_label():
    valid, reason = validate_field_value("Contact Name", "Steamship Line: CMA CGM")
    assert valid is False
    assert reason


def test_contact_name_rejects_any_labeled_operational_line_generalized():
    # Different label, different carrier - proves the fix is general, not a
    # one-off string match on the fixture's own values.
    valid, _ = validate_field_value("Contact Name", "Carrier: ONE")
    assert valid is False


def test_contact_name_still_accepts_a_real_person_name():
    valid, _ = validate_field_value("Contact Name", "Maria Gonzalez")
    assert valid is True


# --- Reference Number must not accept ordinary prose ----------------------


def test_reference_number_rejects_a_prose_sentence_using_the_word_order():
    valid, _ = validate_field_value(
        "Reference Number", "has been entered.", evidence="Please confirm once the order has been entered."
    )
    assert valid is False


def test_reference_number_rejects_a_different_prose_sentence_using_the_word_order():
    valid, _ = validate_field_value(
        "Reference Number",
        "information remains unchanged.",
        evidence="All other order information remains unchanged.",
    )
    assert valid is False


def test_reference_number_candidate_from_prose_is_rejected_end_to_end():
    text = "Please confirm once the order has been entered."
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert "Reference Number" not in selected


def test_reference_number_rejects_a_subject_title_separator_after_order():
    # "New Import Order - Attached Booking Document" - the dash here is a
    # generic subject-title separator, not a reference label, even though
    # the word "order" appears right before it.
    valid, _ = validate_field_value(
        "Reference Number",
        "Attached Booking Document",
        evidence="New Import Order - Attached Booking Document",
    )
    assert valid is False


def test_reference_number_still_accepts_a_genuinely_labeled_value():
    valid, _ = validate_field_value("Reference Number", "SO217089A/C25749C", evidence="Reference Number: SO217089A/C25749C")
    assert valid is True


# --- Address must not collide with a distinct Pickup Address --------------


def test_address_field_ignores_pickup_address_and_keeps_delivery_address():
    text = (
        "Pickup Address: 8700 Wallisville Road, Houston, TX 77029\n"
        "Delivery Address: 15500 North Freeway, Houston, TX 77090"
    )
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert selected.get("Address") == "15500 North Freeway, Houston, TX 77090"


def test_address_field_ignores_pickup_address_generalized_different_values():
    text = (
        "Pickup Address: 1200 Industrial Parkway, Pasadena, TX 77503\n"
        "Delivery Address: 9500 Old Galveston Road, Houston, TX 77034"
    )
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert selected.get("Address") == "9500 Old Galveston Road, Houston, TX 77034"


# --- Port/Terminal must not swallow a distinct Full Return Terminal -------


def test_port_field_does_not_capture_full_return_terminal():
    text = "FULL RETURN: Bayport Terminal"
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert "Port" not in selected


def test_port_field_still_captures_a_real_port_label():
    text = "Port: Bayport Container Terminal"
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert selected.get("Port") == "Bayport Container Terminal"
