"""Regression tests for the fast, non-LLM triage pipeline used during email
sync and Recheck Groups (services/operations_email_triage_service.py).

This is a second, independent classification path from classify_customer_request
(see test_operations_classification.py) that must reach the same verdict for
the same precedence rules: booking confirmations outrank passive billing
language, and quote requests are not misrouted into booking confirmations.
"""
from services.operations_email_triage_service import (
    has_actual_billing_request,
    is_booking_confirmation,
    triage_operations_email,
)
from tests.fixtures.ricgx1235800 import BODY, SUBJECT


def test_booking_confirmation_signal_detected_for_fixture():
    assert is_booking_confirmation(SUBJECT, BODY) is True


def test_fixture_has_no_actual_billing_request():
    assert has_actual_billing_request(SUBJECT, BODY) is False


def test_triage_routes_booking_confirmation_to_new_booking():
    result = triage_operations_email(subject=SUBJECT, body=BODY)
    assert result["request_type"] == "New Booking"
    assert result["work_queue"] != "Billing"


def test_triage_routes_actual_invoice_request_to_billing():
    subject = "Re: RICGX1235800 - please correct invoice"
    body = "Please correct the invoice for booking RICGX1235800, the AES fee is wrong."
    result = triage_operations_email(subject=subject, body=body)
    assert result["request_type"] == "Billing"


def test_triage_does_not_let_quote_request_look_like_a_booking():
    subject = "Rate request - Houston to Kaohsiung 40HC"
    body = "Can you please send a quote for 2x40HC export Houston to Kaohsiung."
    result = triage_operations_email(subject=subject, body=body)
    assert result["request_type"] == "Quote Request"
