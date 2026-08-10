"""_ops_merge_parsed_fields let the AI hybrid body parser's guess fill an
empty Customer field with a bare service-type phrase pulled from the
subject line (e.g. "OTR IMPORT" from "...// OTR IMPORT"), which then got
persisted to order_intake.parsed_data as if it were a real customer name.
"""
from pages_app.operations_inbox import _ops_merge_parsed_fields


def test_customer_is_not_set_from_otr_import_service_phrase():
    merged = _ops_merge_parsed_fields({}, {"Customer": "OTR IMPORT"}, force=False)
    assert merged.get("Customer", "") == ""


def test_customer_is_not_set_from_bare_local_import_phrase():
    merged = _ops_merge_parsed_fields({}, {"Customer": "LOCAL IMPORT"}, force=False)
    assert merged.get("Customer", "") == ""


def test_customer_is_not_set_from_bare_export_phrase_even_with_force():
    merged = _ops_merge_parsed_fields({}, {"Customer": "Export"}, force=True)
    assert merged.get("Customer", "") == ""


def test_customer_is_not_set_from_drayage_phrase():
    merged = _ops_merge_parsed_fields({}, {"customer": "Drayage"}, force=False)
    assert merged.get("customer", "") == ""


def test_real_customer_name_containing_import_substring_is_preserved():
    merged = _ops_merge_parsed_fields({}, {"Customer": "Import Trading Co"}, force=False)
    assert merged["Customer"] == "Import Trading Co"


def test_real_customer_name_still_fills_blank_field():
    merged = _ops_merge_parsed_fields({}, {"Customer": "Flat World Global Logistics"}, force=False)
    assert merged["Customer"] == "Flat World Global Logistics"


def test_non_customer_fields_are_unaffected_by_the_denylist():
    merged = _ops_merge_parsed_fields({}, {"Dispatcher Notes": "Export"}, force=False)
    assert merged["Dispatcher Notes"] == "Export"
