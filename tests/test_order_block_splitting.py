"""Tests for CASE-010's multi-order email split detection: slicing a body
into per-block text segments on explicit "Order N" headers, and a narrow
customer-name prose fallback for emails that state a company name only in
prose (e.g. "...orders for Apex Retail.") with no Customer: label.
"""
from services.email_parser import detect_order_blocks


def test_returns_none_for_zero_headers():
    assert detect_order_blocks("Booking Number: A\nContainer Number: B\n") is None


def test_returns_none_for_a_single_header():
    body = "Order 1\nBooking Number: A\nContainer Number: B\n"
    assert detect_order_blocks(body) is None


def test_splits_two_blocks_without_cross_contamination():
    body = (
        "Please enter these two orders.\n\n"
        "Order 1\n"
        "Booking Number: APEX-260810\n"
        "Container Number: HLXU3000001\n\n"
        "Order 2\n"
        "Booking Number: APEX-260811\n"
        "Container Number: HLXU3000002\n"
    )
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert len(blocks) == 2
    assert "APEX-260810" in blocks[0]
    assert "HLXU3000001" in blocks[0]
    assert "APEX-260811" not in blocks[0]
    assert "HLXU3000002" not in blocks[0]
    assert "APEX-260811" in blocks[1]
    assert "HLXU3000002" in blocks[1]
    assert "APEX-260810" not in blocks[1]
    assert "HLXU3000001" not in blocks[1]


def test_shared_preamble_is_present_in_every_block():
    body = (
        "Please enter these two orders for Apex Retail.\n\n"
        "Order 1\nBooking Number: A\n\n"
        "Order 2\nBooking Number: B\n"
    )
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert "for Apex Retail" in blocks[0]
    assert "for Apex Retail" in blocks[1]


def test_ignores_non_numeric_headers():
    body = "Order Alpha\nBooking Number: A\nOrder Beta\nBooking Number: B\n"
    assert detect_order_blocks(body) is None


def test_allows_exactly_ten_blocks():
    body = "\n".join(f"Order {i}\nBooking Number: B{i}" for i in range(1, 11))
    blocks = detect_order_blocks(body)
    assert blocks is not None
    assert len(blocks) == 10


def test_returns_none_above_ten_blocks():
    body = "\n".join(f"Order {i}\nBooking Number: B{i}" for i in range(1, 12))
    assert detect_order_blocks(body) is None


def test_returns_none_for_empty_body():
    assert detect_order_blocks("") is None


from services.email_parser import _customer_from_prose, parse_email_text


def test_customer_from_prose_matches_trailing_for_phrase():
    text = "Please enter these two separate import orders for Apex Retail."
    assert _customer_from_prose(text) == "Apex Retail"


def test_customer_from_prose_no_trailing_period():
    text = "Booking Number: A\nPlease ship this for Continental Industries Group\n"
    assert _customer_from_prose(text) == "Continental Industries Group"


def test_customer_from_prose_no_match_returns_empty():
    assert _customer_from_prose("No company mentioned here at all.") == ""


def test_parse_email_text_uses_prose_fallback_when_no_customer_label():
    body = (
        "Please enter these two separate import orders for Apex Retail.\n"
        "Booking Number: APEX-260810\n"
    )
    parsed = parse_email_text("", body)
    assert parsed["Customer"] == "Apex Retail"


def test_parse_email_text_label_still_wins_over_prose_fallback():
    body = "Customer: Real Customer Inc\nPlease enter this for Apex Retail.\n"
    parsed = parse_email_text("", body)
    assert parsed["Customer"] == "Real Customer Inc"
