from services.dispatch_legacy_status import map_legacy_status


def test_ready_to_dispatch_maps_unchanged():
    assert map_legacy_status("Ready to Dispatch", "Import") == ("Ready to Dispatch", "Not Started")


def test_assigned_maps_to_driver_assigned():
    assert map_legacy_status("Assigned", "Import") == ("Driver Assigned", "Not Started")
    assert map_legacy_status("Driver Assigned", "Local Import") == ("Driver Assigned", "Not Started")


def test_en_route_to_pickup_is_move_type_specific():
    assert map_legacy_status("En Route to Pickup", "Import") == ("En Route to Port", "Not Started")
    assert map_legacy_status("En Route to Pickup", "Export") == ("En Route to Pickup Warehouse", "Not Started")
    assert map_legacy_status("En Route to Pickup", "Local Import") == ("En Route to Origin Warehouse", "Not Started")


def test_delivered_sets_closeout_pod_needed():
    assert map_legacy_status("Delivered", "Import") == ("Delivered", "POD Needed")


def test_pod_received_maps_to_dispatch_complete_and_pod_received():
    assert map_legacy_status("POD Received", "Export") == ("Dispatch Complete", "POD Received")


def test_ready_for_profittools_maps_to_dispatch_complete_and_closeout():
    assert map_legacy_status("Ready for ProfitTools", "Import") == ("Dispatch Complete", "Ready for ProfitTools")


def test_invoiced_and_closed_map_to_closed_closeout():
    assert map_legacy_status("Invoiced", "Export") == ("Dispatch Complete", "Closed")
    assert map_legacy_status("Closed", "Local Export") == ("Dispatch Complete", "Closed")


def test_cancelled_is_unchanged():
    assert map_legacy_status("Cancelled", "Import") == ("Cancelled", "Not Started")


def test_pre_dispatch_statuses_return_empty_operational_status():
    for legacy in ["New", "Hold/Need Info", "Booking Verified", "Port Verified", "PIN Received"]:
        new_status, closeout = map_legacy_status(legacy, "Import")
        assert new_status == ""
        assert closeout == "Not Started"


def test_dispatched_maps_to_first_en_route_stage_per_move_type():
    assert map_legacy_status("Dispatched", "Import") == ("En Route to Port", "Not Started")
    assert map_legacy_status("Dispatched", "Export") == ("En Route to Pickup Warehouse", "Not Started")
