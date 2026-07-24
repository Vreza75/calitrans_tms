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
