from services.dispatch_stages import (
    CANCELLED_STATUS,
    COMPLETION_STATUS,
    get_operational_stages,
    validate_transition,
)


def test_import_stage_order():
    stages = get_operational_stages("Import")
    assert stages == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Port",
        "At Port",
        "Container Picked Up",
        "En Route to Delivery Warehouse",
        "At Delivery Warehouse",
        "Delivered",
        "Returning Empty",
        "Empty Returned",
        "Dispatch Complete",
    ]


def test_export_stage_order():
    stages = get_operational_stages("Export")
    assert stages == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Pickup Warehouse",
        "At Pickup Warehouse",
        "Container Loaded",
        "En Route to Port",
        "At Port",
        "In-Gated",
        "Dispatch Complete",
    ]


def test_local_import_and_local_export_share_stage_shape():
    assert get_operational_stages("Local Import") == get_operational_stages("Local Export")
    assert get_operational_stages("Local Import") == [
        "Ready to Dispatch",
        "Driver Assigned",
        "En Route to Origin Warehouse",
        "At Origin Warehouse",
        "Loaded / Picked Up",
        "En Route to Destination Warehouse",
        "At Destination Warehouse",
        "Delivered",
        "Dispatch Complete",
    ]


def test_unknown_move_type_falls_back_to_local_import_shape():
    assert get_operational_stages("Other") == get_operational_stages("Local Import")


def test_cannot_assign_without_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=False, has_truck=True)
    assert ok is False
    assert "driver" in reason.lower()


def test_cannot_assign_without_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=True, has_truck=False)
    assert ok is False


def test_can_assign_with_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Driver Assigned", has_driver=True, has_truck=True)
    assert ok is True
    assert reason == ""


def test_cannot_go_en_route_without_origin():
    ok, reason = validate_transition(
        "Import", "Driver Assigned", "En Route to Port",
        has_driver=True, has_truck=True, has_origin=False,
    )
    assert ok is False
    assert "origin" in reason.lower()


def test_cannot_reach_at_pickup_before_assigned():
    ok, reason = validate_transition("Export", "Ready to Dispatch", "At Pickup Warehouse", has_driver=False, has_truck=False)
    assert ok is False


def test_import_cannot_return_empty_before_delivered():
    ok, reason = validate_transition(
        "Import", "At Delivery Warehouse", "Returning Empty",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False
    assert "delivered" in reason.lower()


def test_import_can_return_empty_after_delivered():
    ok, reason = validate_transition(
        "Import", "Delivered", "Returning Empty",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is True


def test_export_cannot_in_gate_before_at_port():
    ok, reason = validate_transition(
        "Export", "En Route to Port", "In-Gated",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False
    assert "port" in reason.lower()


def test_export_can_in_gate_after_at_port():
    ok, reason = validate_transition(
        "Export", "At Port", "In-Gated",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is True


def test_import_cannot_complete_before_delivered():
    ok, reason = validate_transition(
        "Import", "Container Picked Up", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False


def test_import_complete_requires_empty_returned_when_required():
    ok, reason = validate_transition(
        "Import", "Delivered", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True, empty_return_required=True,
    )
    assert ok is False
    assert "empty returned" in reason.lower()


def test_import_complete_ok_from_delivered_when_no_empty_return_required():
    ok, reason = validate_transition(
        "Import", "Delivered", "Dispatch Complete",
        has_driver=True, has_truck=True, has_origin=True, empty_return_required=False,
    )
    assert ok is True


def test_completed_load_blocks_further_operational_transitions():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Port", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_completed_load_allows_transition_with_override():
    ok, reason = validate_transition(
        "Import", COMPLETION_STATUS, "En Route to Port",
        has_driver=True, has_truck=True, has_origin=True, override=True,
    )
    assert ok is True


def test_cancel_allowed_from_active_status():
    ok, reason = validate_transition("Import", "En Route to Port", CANCELLED_STATUS)
    assert ok is True


def test_cannot_cancel_a_completed_load():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, CANCELLED_STATUS)
    assert ok is False


def test_backward_transition_blocked_without_override():
    ok, reason = validate_transition(
        "Import", "At Port", "Driver Assigned",
        has_driver=True, has_truck=True, has_origin=True,
    )
    assert ok is False


def test_backward_transition_allowed_with_override():
    ok, reason = validate_transition(
        "Import", "At Port", "Driver Assigned",
        has_driver=True, has_truck=True, has_origin=True, override=True,
    )
    assert ok is True


def test_unknown_new_status_rejected():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Not A Real Status")
    assert ok is False
