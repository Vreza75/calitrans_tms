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


# --- Quote-lane detection: case-insensitive, history-aware, English/Spanish -


def test_quote_request_lowercase_lane_still_routes_to_quote_request():
    subject = "quote request"
    body = "please quote houston to dallas 40hc."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_quote_request_uppercase_lane_still_routes_to_quote_request():
    subject = "QUOTE REQUEST"
    body = "PLEASE QUOTE HOUSTON TO DALLAS 40HC."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_quote_request_mixed_case_lane_still_routes_to_quote_request():
    subject = "Quote Request"
    body = "PleAsE QuoTe HouSTon TO daLLAs 40hc."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_quote_request_from_x_to_y_phrasing_routes_to_quote_request():
    subject = "Rate request"
    body = "Rate request from Houston to Dallas."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_quote_request_need_pricing_phrasing_routes_to_quote_request():
    subject = "Pricing"
    body = "Need pricing from houston to dallas."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_quote_request_arrow_lane_routes_to_quote_request():
    subject = "Quote"
    body = "Quote Houston → Dallas."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_spanish_quote_request_routes_to_quote_request():
    subject = "Cotizacion"
    body = "Por favor cotizar Houston a Dallas."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_spanish_rate_request_routes_to_quote_request():
    subject = "Tarifa"
    body = "Necesito tarifa de Houston a Dallas."
    assert classify_customer_request(subject, body) == "Quote Request"


def test_person_handoff_phrase_does_not_route_to_quote_request():
    subject = "FYI"
    body = "Please quote this shipment. John to Maria, FYI."
    assert classify_customer_request(subject, body) != "Quote Request"


def test_weekday_range_does_not_route_to_quote_request():
    subject = "Availability"
    body = "We are available Monday to Friday for pickup."
    assert classify_customer_request(subject, body) != "Quote Request"


def test_time_range_does_not_route_to_quote_request():
    subject = "Shift"
    body = "Drivers are on shift 8 AM to 5 PM."
    assert classify_customer_request(subject, body) != "Quote Request"


def test_reply_routing_phrase_does_not_route_to_quote_request():
    subject = "Re: your message"
    body = "Please reply to customer directly."
    assert classify_customer_request(subject, body) != "Quote Request"


def test_forward_routing_phrase_does_not_route_to_quote_request():
    subject = "Fwd"
    body = "Please send to accounting for processing."
    assert classify_customer_request(subject, body) != "Quote Request"


def test_quoted_history_only_quote_request_does_not_contaminate_classification():
    subject = "Re: shipment"
    body = (
        "Thanks for the update, no changes needed right now.\n\n"
        "On Mon, Jul 27, 2026 at 9:00 AM, Customer <customer@example.com> wrote:\n"
        "> Please quote Houston to Dallas 40HC.\n"
        "> Let me know the rate.\n"
    )
    assert classify_customer_request(subject, body) != "Quote Request"


def test_existing_load_update_with_old_quoted_pricing_stays_booking_update():
    subject = "Delivery date change"
    body = (
        "Please update the delivery date for booking GCR-IMP-260801 to August 10.\n\n"
        "On Mon, Jul 20, 2026, Customer <customer@example.com> wrote:\n"
        "> Please quote Houston to Dallas 40HC.\n"
    )
    assert classify_customer_request(subject, body) == "Booking Update"


def test_new_booking_with_no_quote_intent_is_not_quote_request():
    subject = "New Booking"
    body = "Please create a new import order. Booking Number: ABC123. Container Number: MSCU1234567."
    assert classify_customer_request(subject, body) == "New Booking"
