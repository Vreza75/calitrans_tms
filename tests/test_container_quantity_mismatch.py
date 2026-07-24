"""Tests for CASE-007's container-quantity-mismatch detection: extracting
every container number mentioned (not just the first), a sentence-based
quantity fallback, and the pure comparison that decides whether a
declared quantity and the detected container numbers disagree.
"""
from services.email_parser import _all_container_numbers, parse_email_text


def test_all_container_numbers_finds_every_distinct_token_in_order():
    text = """
    Containers:
    TEMU2000001 - 40HC
    TEMU2000002 - 40HC
    TEMU2000003 - 40HC
    """
    assert _all_container_numbers(text) == ["TEMU2000001", "TEMU2000002", "TEMU2000003"]


def test_all_container_numbers_deduplicates():
    text = "Container Number: MSCU1234567\nPlease confirm MSCU1234567 is correct."
    assert _all_container_numbers(text) == ["MSCU1234567"]


def test_all_container_numbers_empty_when_none_present():
    assert _all_container_numbers("No containers mentioned here.") == []


def test_parse_email_text_populates_container_numbers_field():
    body = (
        "Customer: Summit Furniture Imports\n"
        "Booking Number: QTY-260807\n"
        "Containers:\n"
        "TEMU2000001 - 40HC\n"
        "TEMU2000002 - 40HC\n"
        "TEMU2000003 - 40HC\n"
    )
    parsed = parse_email_text("", body)
    assert parsed["Container Numbers"] == ["TEMU2000001", "TEMU2000002", "TEMU2000003"]
    # Existing singular field is untouched - still the first one found.
    assert parsed["Container Number"] == "TEMU2000001"


def test_parse_email_text_single_container_still_works():
    body = "Booking Number: GCR-IMP-260801\nContainer Number: MSCU1234567\n"
    parsed = parse_email_text("", body)
    assert parsed["Container Numbers"] == ["MSCU1234567"]
    assert parsed["Container Number"] == "MSCU1234567"


from services.email_parser import _container_qty_from_sentence


def test_container_qty_from_sentence_total_quantity_phrasing():
    text = "Containers:\nTEMU2000001 - 40HC\n\nTotal quantity: 4 containers."
    assert _container_qty_from_sentence(text) == "4"


def test_container_qty_from_sentence_n_containers_total_phrasing():
    assert _container_qty_from_sentence("We are shipping 6 containers total this week.") == "6"


def test_container_qty_from_sentence_bare_quantity_label():
    assert _container_qty_from_sentence("Quantity: 3\nRest of the email.") == "3"


def test_container_qty_from_sentence_no_match_returns_empty():
    assert _container_qty_from_sentence("No quantity mentioned anywhere here.") == ""


def test_parse_email_text_uses_sentence_fallback_only_when_label_is_absent():
    # CASE-007's actual fixture body: no "Container Qty:"/"Number Of Cntrs:"
    # label, only the free-text closing sentence.
    body = (
        "Customer: Summit Furniture Imports\n"
        "Booking Number: QTY-260807\n"
        "Terminal: Barbours Cut Terminal\n"
        "Delivery Address: 7200 West Road, Houston, TX 77086\n\n"
        "Containers:\n\n"
        "TEMU2000001 - 40HC\n"
        "TEMU2000002 - 40HC\n"
        "TEMU2000003 - 40HC\n\n"
        "Total quantity: 4 containers.\n"
    )
    parsed = parse_email_text("", body)
    assert parsed["Container Qty"] == "4"


def test_parse_email_text_label_still_wins_over_sentence_fallback():
    body = "Number Of Cntrs: 4 X 40HC\nWe are shipping 6 containers total this week."
    parsed = parse_email_text("", body)
    assert parsed["Container Qty"] == "4"
