"""The pending-draft summary used to render a hardcoded "Export / Booking
Details" header (and export-only fields: Empty Pickup, Full Return,
Document/VGM/Cargo Cutoff, Sailing, ETA, Carrier) for every service flow,
including Local Import - regardless of what the booking actually was.
"""
from pages_app.operations_inbox import _ops_route_cargo_section_title


def test_export_gets_export_booking_details_title():
    assert _ops_route_cargo_section_title("Export") == "Export Booking Details"


def test_import_gets_import_route_details_title():
    assert _ops_route_cargo_section_title("Import") == "Import Route Details"


def test_local_import_gets_its_own_title_not_export():
    title = _ops_route_cargo_section_title("Local Import")
    assert title == "Local Import Route and Cargo Details"
    assert "Export" not in title


def test_local_export_gets_its_own_title_not_export_booking():
    title = _ops_route_cargo_section_title("Local Export")
    assert title == "Local Export Route and Cargo Details"
    assert "Booking" not in title


def test_unknown_service_flow_gets_a_neutral_title():
    assert _ops_route_cargo_section_title("") == "Route and Cargo Details"
