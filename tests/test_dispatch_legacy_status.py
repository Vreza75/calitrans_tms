from services.dispatch_legacy_status import map_legacy_status


def test_ready_to_dispatch_unchanged():
    assert map_legacy_status("Ready to Dispatch", "Import") == ("Ready to Dispatch", "Not Started")


def test_assigned_maps_to_ready_to_dispatch_not_a_stage():
    assert map_legacy_status("Assigned", "Import") == ("Ready to Dispatch", "Not Started")
    assert map_legacy_status("Driver Assigned", "Export") == ("Ready to Dispatch", "Not Started")


def test_en_route_to_pickup_same_for_every_move_type():
    for move_type in ["Import", "Export", "Local Import", "Local Export"]:
        assert map_legacy_status("En Route to Pickup", move_type) == ("En Route to Pickup", "Not Started")
    assert map_legacy_status("Dispatched", "Import") == ("En Route to Pickup", "Not Started")


def test_at_port_and_loaded_both_map_to_at_pickup():
    assert map_legacy_status("At Port", "Import") == ("At Pickup", "Not Started")
    assert map_legacy_status("Loaded", "Export") == ("At Pickup", "Not Started")
    assert map_legacy_status("Loaded / Picked Up", "Local Import") == ("At Pickup", "Not Started")


def test_delivered_maps_to_completed_with_pod_needed():
    assert map_legacy_status("Delivered", "Import") == ("Completed", "POD Needed")


def test_returning_empty_unchanged():
    assert map_legacy_status("Returning Empty", "Import") == ("Returning Empty", "POD Needed")


def test_pod_received_maps_to_completed_pod_received():
    assert map_legacy_status("POD Received", "Export") == ("Completed", "POD Received")


def test_ready_for_profittools_and_exported_map_to_completed_ready_for_profittools():
    assert map_legacy_status("Ready for ProfitTools", "Import") == ("Completed", "Ready for ProfitTools")
    assert map_legacy_status("Exported to ProfitTools", "Export") == ("Completed", "Ready for ProfitTools")


def test_invoiced_and_closed_map_to_completed_closed():
    assert map_legacy_status("Invoiced", "Export") == ("Completed", "Closed")
    assert map_legacy_status("Closed", "Local Export") == ("Completed", "Closed")


def test_cancelled_unchanged():
    assert map_legacy_status("Cancelled", "Import") == ("Cancelled", "Not Started")


def test_pre_dispatch_statuses_return_empty_operational_status():
    for legacy in ["New", "Hold/Need Info", "Booking Verified", "Port Verified", "PIN Received"]:
        assert map_legacy_status(legacy, "Import") == ("", "Not Started")
