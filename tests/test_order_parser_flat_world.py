"""order_parser.py's is_flat_world block used `[^\\n]+` (greedy to end of
line) for several fields. The actual PDF's two-column layout gets flattened
by pdfplumber into two "Label: value" pairs sharing one line (e.g. "Ready
Date: 6/30/2026 Customer PO: SSHAES058926 / POL147359"), so those greedy
patterns swallowed the next label's text too. Separately, "Warehouse" held
the pickup location, but the pending-draft field mapper reads Warehouse as
the destination and Port as the origin - so the origin never showed at all,
and the destination field showed the wrong location.
"""
from services.order_parser import parse_order_text
from tests.fixtures.flat_world_rate_confirmation import TEXT


def test_ready_date_does_not_swallow_the_customer_po_on_the_same_line():
    parsed = parse_order_text(TEXT)
    assert parsed["Delivery Need Date"] == "6/30/2026"


def test_reference_number_is_unaffected_by_the_date_fix():
    parsed = parse_order_text(TEXT)
    assert parsed["Reference Number"] == "SSHAES058926 / POL147359"


def test_pickup_origin_is_populated_without_the_contact_label():
    parsed = parse_order_text(TEXT)
    assert parsed["Port"] == "BAYPORT CONTAINER TERMINAL"


def test_pickup_address_is_populated_across_the_wrapped_line():
    parsed = parse_order_text(TEXT)
    assert "1515 E BARBOURS CUT BLVD" in parsed["Address"]
    assert "LA PORTE, TX 77571" in parsed["Address"]


def test_destination_is_the_delivery_location_not_the_pickup_location():
    parsed = parse_order_text(TEXT)
    assert parsed["Warehouse"] == "MARSHAL MINERALS"


def test_container_number_and_booking_number_still_extract_correctly():
    parsed = parse_order_text(TEXT)
    assert parsed["Container Number"] == "GAOU7296662"
    assert parsed["Booking Number"] == "130067971"
