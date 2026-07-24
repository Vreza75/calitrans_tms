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


from services.email_parser import detect_container_quantity_mismatch


def test_no_mismatch_when_quantity_stated_and_zero_numbers_found():
    # RICGX1235800 / CASE-006's shape: quantity known, carrier hasn't
    # issued physical numbers yet. This must NOT be flagged.
    parsed = {"Container Qty": "4", "Container Numbers": []}
    assert detect_container_quantity_mismatch(parsed) is None


def test_mismatch_when_fewer_numbers_found_than_declared():
    # CASE-007's shape: 4 declared, 3 listed.
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C"]}
    result = detect_container_quantity_mismatch(parsed)
    assert result == {
        "declared": 4,
        "found": 3,
        "message": (
            "Quantity mismatch: 4 declared, 3 container numbers found - "
            "confirm before creating order."
        ),
    }


def test_no_mismatch_when_fully_specified():
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C", "D"]}
    assert detect_container_quantity_mismatch(parsed) is None


def test_mismatch_when_more_numbers_found_than_declared():
    parsed = {"Container Qty": "3", "Container Numbers": ["A", "B", "C", "D"]}
    result = detect_container_quantity_mismatch(parsed)
    assert result == {
        "declared": 3,
        "found": 4,
        "message": (
            "Quantity mismatch: 3 declared, 4 container numbers found - "
            "confirm before creating order."
        ),
    }


def test_no_mismatch_when_no_quantity_stated_at_all():
    parsed = {"Container Qty": "", "Container Numbers": ["A"]}
    assert detect_container_quantity_mismatch(parsed) is None


def test_singular_container_word_in_message():
    result = detect_container_quantity_mismatch(
        {"Container Qty": "2", "Container Numbers": ["A"]}
    )
    assert result["message"] == (
        "Quantity mismatch: 2 declared, 1 container number found - "
        "confirm before creating order."
    )


from services.operations_inbox_service import enforce_container_quantity_mismatch_review


def test_enforce_review_sets_review_fields_on_mismatch():
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C"]}
    triage = {"request_type": "New Booking", "work_queue": "New Orders", "llm_review_required": False}

    result = enforce_container_quantity_mismatch_review(parsed, triage)

    assert result["llm_review_required"] is True
    assert result["work_queue"] == "Review"
    assert "4 declared, 3 container numbers found" in result["action_required"]
    assert "4 declared, 3 container numbers found" in result["triage_reason"]
    # Request type itself is left alone - only the review-routing fields change.
    assert result["request_type"] == "New Booking"


def test_enforce_review_is_a_no_op_when_no_mismatch():
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C", "D"]}
    triage = {"request_type": "New Booking", "work_queue": "New Orders", "llm_review_required": False}

    result = enforce_container_quantity_mismatch_review(parsed, triage)

    assert result == triage


def test_enforce_review_handles_none_triage():
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C"]}
    result = enforce_container_quantity_mismatch_review(parsed, None)
    assert result["llm_review_required"] is True


def test_enforce_review_does_not_override_unrelated_request_types():
    parsed = {"Container Qty": "4", "Container Numbers": ["A", "B", "C"]}
    triage = {"request_type": "Billing", "work_queue": "Billing", "llm_review_required": False}

    result = enforce_container_quantity_mismatch_review(parsed, triage)

    assert result == triage


def test_lower_blob_flattens_list_values_without_python_repr_syntax():
    from services.operations_email_triage_service import _lower_blob

    parsed = {"Container Numbers": ["TEMU2000001", "TEMU2000002"], "Port": ""}
    blob = _lower_blob("subject", "body", parsed)

    assert "[" not in blob
    assert "]" not in blob
    assert "'" not in blob
    assert "temu2000001" in blob
    assert "temu2000002" in blob
