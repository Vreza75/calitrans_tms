"""Regression tests for parsing multi-container booking fields out of the
RICGX1235800 fixture. Previously: Size dropped the HC/HQ suffix ("40" instead
of "40HC") and there was no Container Qty field at all anywhere in the
deterministic parser, so quantity was lost entirely upstream of any "default
to 1" behavior downstream.
"""
from services.email_parser import parse_email_text
from tests.fixtures.ricgx1235800 import BODY, SUBJECT


def test_container_qty_and_size_parsed_from_number_of_cntrs_line():
    parsed = parse_email_text(SUBJECT, BODY)
    assert parsed["Container Qty"] == "4"
    assert parsed["Size"] == "40HC"


def test_size_regex_keeps_hc_suffix_when_no_qty_prefix():
    parsed = parse_email_text("", "Equipment: 40HC container needed for pickup.")
    assert parsed["Size"] == "40HC"


def test_invalid_container_qty_value_is_rejected_not_polluted():
    parsed = parse_email_text("", "Number Of Cntrs: TBD\nBooking Number: RICGX1235800")
    assert parsed["Container Qty"] == ""


def test_full_return_terminal_is_captured_and_isolated_from_port():
    # RICGX1235800's real body has "FULL RETURN: Bayport Terminal" - it must
    # land in its own dedicated field, not the pickup/POL Port field (which
    # this fixture has no explicit "Port:"/"Terminal:" label for at all).
    parsed = parse_email_text(SUBJECT, BODY)
    assert parsed["Full Return Terminal"] == "Bayport Terminal"
    assert parsed["Port"] == ""
