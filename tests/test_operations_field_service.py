"""Regression coverage for services/operations_field_service.py's shared
candidate-generation/validation pipeline, covering defects found while
certifying the Operations Inbox intake pipeline against a disposable
Postgres database (see docs/reviews/OPERATIONS_INBOX_CERTIFICATION_INVESTIGATION.md
and docs/reviews/OPERATIONS_INBOX_CERTIFICATION_EDGE_CORRECTIONS.md).
"""
import pytest

from services.operations_field_service import (
    generate_field_candidates,
    select_field_candidates,
    validate_field_value,
)


def _reference_number(text: str) -> str | None:
    candidates = generate_field_candidates(newest_message=text)
    return select_field_candidates(candidates).get("Reference Number")


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


def test_reference_number_rejects_a_stringified_parsed_dict_key():
    # Several call sites in operations_inbox_service.py embed str(parsed)
    # back into text for re-scanning - a dict repr like
    # "'Reference Number': ''" must never let the label's own continuation
    # word ("Number") get captured as if it were the value.
    text = "{'TYPE': 'Export', 'Booking Number': '', 'Reference Number': '', 'Container Number': ''}"
    assert _reference_number(text) is None


# --- Reference Number: valid labeled forms, English and Spanish -----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Reference: ABC", "ABC"),
        ("Reference Number: ABC", "ABC"),
        ("Reference No.: ABC", "ABC"),
        ("Reference #: ABC", "ABC"),
        ("Ref: ABC", "ABC"),
        ("PO: ABC", "ABC"),
        ("Order: ABC", "ABC"),
        ("Order #: ABC", "ABC"),
        ("Order No.: ABC", "ABC"),
        ("Order # ABC", "ABC"),
        ("Shipment: ABC", "ABC"),
        ("Referencia: ABC", "ABC"),
        ("No. de Referencia: ABC", "ABC"),
        ("Referencia No. ABC", "ABC"),
        ("Orden: ABC", "ABC"),
        ("Pedido: ABC", "ABC"),
        ("Reference: ABC-123", "ABC-123"),
        ("Reference: SO217089A/C25749C", "SO217089A/C25749C"),
        ("Reference: REF_2026", "REF_2026"),
        ("Reference: A.B.C", "A.B.C"),
        ("Reference: 12345", "12345"),
    ],
)
def test_reference_number_accepts_valid_labeled_forms(text, expected):
    assert _reference_number(text) == expected


# --- Reference Number: prose false positives stay rejected -----------------


@pytest.mark.parametrize(
    "text",
    [
        "The order has been entered.",
        "All other order information remains unchanged.",
        "Please enter the order tomorrow.",
        "Reference the previous email.",
        "Reference the prior message.",
        "The shipment has arrived.",
        "New Import Order - Attached Booking Document.",
        "Order - Attached Document.",
    ],
)
def test_reference_number_rejects_prose_false_positives(text):
    assert _reference_number(text) is None


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


# --- Address precedence: deterministic regardless of label order ----------


def _address(text: str) -> str | None:
    candidates = generate_field_candidates(newest_message=text)
    return select_field_candidates(candidates).get("Address")


def test_delivery_beats_warehouse_when_warehouse_appears_first():
    text = "Warehouse Address: 100 Warehouse Road\nDelivery Address: 200 Customer Street"
    assert _address(text) == "200 Customer Street"


def test_delivery_beats_warehouse_when_delivery_appears_first():
    text = "Delivery Address: 200 Customer Street\nWarehouse Address: 100 Warehouse Road"
    assert _address(text) == "200 Customer Street"


def test_delivery_beats_pickup_when_pickup_appears_first():
    text = "Pickup Address: 8700 Wallisville Road\nDelivery Address: 15500 North Freeway"
    assert _address(text) == "15500 North Freeway"


def test_delivery_beats_pickup_when_delivery_appears_first():
    text = "Delivery Address: 15500 North Freeway\nPickup Address: 8700 Wallisville Road"
    assert _address(text) == "15500 North Freeway"


def test_warehouse_address_is_used_as_fallback_when_alone():
    assert _address("Warehouse Address: 100 Warehouse Road") == "100 Warehouse Road"


def test_delivery_address_alone():
    assert _address("Delivery Address: 200 Customer Street") == "200 Customer Street"


def test_pickup_address_alone_does_not_populate_generic_address():
    assert _address("Pickup Address: 8700 Wallisville Road") is None


def test_pickup_address_alone_still_populates_its_own_pickup_field():
    # Complements the test above: "Pickup Address" must not silently vanish
    # just because it doesn't feed the generic Address field - it still
    # reaches its own dedicated Customer Pickup Address field via the
    # legacy label-alias mechanism in parse_email_text.
    from services.email_parser import parse_email_text

    parsed = parse_email_text("", "Pickup Address: 8700 Wallisville Road")
    assert parsed["Customer Pickup Address"] == "8700 Wallisville Road"
    assert parsed["Address"] == ""


@pytest.mark.parametrize("flow_line", ["Local Import", "Local Export", "Import", "Export"])
def test_delivery_precedence_holds_across_service_flows(flow_line):
    text = f"TYPE: {flow_line}\nWarehouse Address: 100 Warehouse Road\nDelivery Address: 200 Customer Street"
    assert _address(text) == "200 Customer Street"


# --- Multi-line delivery addresses -----------------------------------------


def test_two_line_delivery_address_is_preserved():
    text = "Delivery Address:\n200 Customer Street\nHouston, TX 77001"
    assert _address(text) == "200 Customer Street, Houston, TX 77001"


def test_three_line_delivery_address_with_suite_is_preserved():
    text = "Delivery Address:\n200 Customer Street\nSuite 400\nHouston, TX 77001"
    assert _address(text) == "200 Customer Street, Suite 400, Houston, TX 77001"


def test_delivery_address_first_line_plus_continuation():
    text = "Delivery Address: 200 Customer Street\nHouston, TX 77001"
    assert _address(text) == "200 Customer Street, Houston, TX 77001"


def test_delivery_address_stops_before_next_field_label():
    text = "Delivery Address: 200 Customer Street\nHouston, TX 77001\nContact: Jane Doe"
    result = _address(text)
    assert result == "200 Customer Street, Houston, TX 77001"
    assert "Jane Doe" not in result


def test_delivery_address_stops_at_blank_line():
    text = "Delivery Address: 200 Customer Street\n\nContact: Jane Doe"
    result = _address(text)
    assert result == "200 Customer Street"
    assert "Jane Doe" not in result


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


# --- Full Return Terminal has its own dedicated field ----------------------


@pytest.mark.parametrize(
    "text",
    [
        "Full Return: Bayport Terminal",
        "Full Return Terminal: Bayport Terminal",
        "Return Terminal: Bayport Terminal",
        "Empty Return: Bayport Terminal",
        "Empty Return Terminal: Bayport Terminal",
    ],
)
def test_full_return_maps_to_its_dedicated_field(text):
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert selected.get("Full Return Terminal") == "Bayport Terminal"


def test_full_return_does_not_populate_port():
    text = "FULL RETURN: Bayport Terminal"
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert "Port" not in selected


def test_full_return_does_not_populate_pickup_terminal_field():
    # "Terminal" is the same shared field a bare "Pickup Terminal:" label
    # would populate - Full Return must never feed it either.
    text = "FULL RETURN: Bayport Terminal"
    candidates = generate_field_candidates(newest_message=text)
    selected = select_field_candidates(candidates)
    assert "Terminal" not in selected
