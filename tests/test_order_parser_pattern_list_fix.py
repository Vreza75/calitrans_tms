"""Regression coverage for services/order_parser.py's generic-parser
Customer/Port/Warehouse (and Address/Document Cutoff/LFD) find_pattern
lists: adjacent raw strings with a missing comma silently concatenate
into one regex, and under find_pattern's re.DOTALL, a `(.+)` in that list
can swallow the rest of the entire document once it starts matching.
Both bugs are covered here independently.
"""
from __future__ import annotations

from services.order_parser import parse_order_text


def test_customer_extracted_when_only_second_pattern_would_have_matched():
    """Before the comma fix, `Customer:\\s*([^\\n]+)` and
    `Consignee[:\\s]+(.+)` were silently concatenated into one dead
    pattern requiring both labels on the same line - so a document using
    only "Consignee:" (never "Customer:") could never match via this
    pattern at all. This fixture uses only "Consignee:"."""
    text = "Consignee: Acme Import Co\nBooking Number: BK123456\n"

    parsed = parse_order_text(text)

    assert parsed.get("Customer") == "Acme Import Co"


def test_customer_field_does_not_swallow_unrelated_multiline_content():
    """Consignee[:\\s]+(.+) under re.DOTALL, if left as `(.+)` instead of
    `([^\\n]+)`, would swallow every line after it, not just the
    Consignee line."""
    text = (
        "Consignee: Acme Import Co\n"
        "Booking Number: BK123456\n"
        "Port: Houston\n"
        "Warehouse: PBP Packaging\n"
    )

    parsed = parse_order_text(text)

    assert parsed.get("Customer") == "Acme Import Co"
    assert "Booking Number" not in str(parsed.get("Customer", ""))
    assert "Port:" not in str(parsed.get("Customer", ""))
    assert "Warehouse:" not in str(parsed.get("Customer", ""))


def test_port_extracted_when_only_second_pattern_would_have_matched():
    text = "Terminal: Bayport Terminal\nBooking Number: BK123456\n"

    parsed = parse_order_text(text)

    assert parsed.get("Port") == "Bayport Terminal"


def test_port_field_does_not_swallow_unrelated_multiline_content():
    text = "Terminal: Bayport Terminal\nWarehouse: PBP Packaging\nCustomer: Acme Corp\n"

    parsed = parse_order_text(text)

    assert parsed.get("Port") == "Bayport Terminal"
    assert "Warehouse:" not in str(parsed.get("Port", ""))
    assert "Customer:" not in str(parsed.get("Port", ""))


def test_warehouse_extracted_when_only_second_pattern_would_have_matched():
    text = "Delivery Location: ABC Distribution Center\nBooking Number: BK123456\n"

    parsed = parse_order_text(text)

    assert parsed.get("Warehouse") == "ABC Distribution Center"


def test_warehouse_field_does_not_swallow_unrelated_multiline_content():
    text = "Delivery Location: ABC Distribution Center\nCustomer: Acme Corp\nPort: Houston\n"

    parsed = parse_order_text(text)

    assert parsed.get("Warehouse") == "ABC Distribution Center"
    assert "Customer:" not in str(parsed.get("Warehouse", ""))
    assert "Port:" not in str(parsed.get("Warehouse", ""))


def test_multiple_candidate_fields_in_one_message_all_extracted_independently():
    text = (
        "Customer: Continental Industries Group\n"
        "Booking Number: RICGX1235800\n"
        "Port: Houston\n"
        "Warehouse: PBP Packaging\n"
        "Document Cutoff: 7/22\n"
        "LFD: 7/25\n"
    )

    parsed = parse_order_text(text)

    assert parsed.get("Customer") == "Continental Industries Group"
    assert parsed.get("Port") == "Houston"
    assert parsed.get("Warehouse") == "PBP Packaging"
    assert parsed.get("Document Cutoff") == "7/22"
    assert parsed.get("LFD") == "7/25"


def test_first_pattern_still_wins_when_both_are_present():
    """Preserve existing valid extraction: when the text contains the
    label the FIRST pattern targets, that value must still be used (not
    a fallback second-pattern match)."""
    text = "Customer: Direct Label Wins\nConsignee: Should Not Be Used\n"

    parsed = parse_order_text(text)

    assert parsed.get("Customer") == "Direct Label Wins"


def test_value_near_blank_lines_is_still_captured_on_its_own_line():
    text = "Customer: Acme Corp\n\n\nBooking Number: BK999\n"

    parsed = parse_order_text(text)

    assert parsed.get("Customer") == "Acme Corp"


def test_false_positive_prevention_unrelated_label_does_not_match_customer():
    """A document with structured labels but no Customer/Consignee label
    anywhere must not have some other line's value bleed into Customer
    via find_pattern specifically (parse_order_text's separate
    email-style fallback parser has its own, pre-existing, out-of-scope
    behavior for wholly unstructured text - not exercised here)."""
    text = "Booking Number: BK123456\nPort: Houston\nWarehouse: PBP Packaging\n"

    parsed = parse_order_text(text)

    assert "Houston" not in str(parsed.get("Customer", ""))
    assert "PBP Packaging" not in str(parsed.get("Customer", ""))
    assert "BK123456" not in str(parsed.get("Customer", ""))
