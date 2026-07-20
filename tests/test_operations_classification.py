"""Regression tests for the Operations Inbox classification precedence.

A clear booking confirmation must always outrank passive Billing/BOL/rate-sheet
language. See docs findings: build_operations_email_classification (used by
the queue table / dispatcher decision card) calls classify_customer_request,
which scored "Billing" ahead of "New Booking" for messages like this because
it never consulted is_booking_confirmation()/has_actual_billing_request().
"""
from services.operations_inbox_service import (
    build_operations_email_classification,
    classify_customer_request,
)
from tests.fixtures.ricgx1235800 import BODY, SUBJECT


def test_booking_confirmation_with_bill_of_lading_language_is_new_booking():
    result = classify_customer_request(SUBJECT, BODY)
    assert result == "New Booking"


def test_booking_confirmation_review_classification_is_new_booking():
    classification = build_operations_email_classification(SUBJECT, BODY)
    assert classification["request_type"] == "New Booking"


def test_actual_invoice_request_still_routes_to_billing():
    subject = "Re: RICGX1235800 - please correct invoice"
    body = "Please correct the invoice for booking RICGX1235800, the AES fee is wrong."
    assert classify_customer_request(subject, body) == "Billing"


def test_quote_request_routes_to_quote_request():
    subject = "Rate request - Houston to Kaohsiung 40HC"
    body = "Can you please send a quote for 2x40HC export Houston to Kaohsiung."
    assert classify_customer_request(subject, body) == "Quote Request"
