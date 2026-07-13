from services.dispatch_stages import (
    CANCELLED_STATUS,
    COMPLETION_STATUS,
    get_operational_stages,
    validate_transition,
)


def test_import_includes_returning_empty():
    assert get_operational_stages("Import") == [
        "Ready to Dispatch",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Returning Empty",
        "Completed",
    ]


def test_export_excludes_returning_empty():
    assert get_operational_stages("Export") == [
        "Ready to Dispatch",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Completed",
    ]


def test_local_import_and_export_share_stage_list():
    assert get_operational_stages("Local Import") == get_operational_stages("Local Export")
    assert "Returning Empty" not in get_operational_stages("Local Import")


def test_cannot_start_en_route_to_pickup_without_driver():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=False, has_truck=True, has_origin=True)
    assert ok is False
    assert "driver" in reason.lower()


def test_cannot_start_en_route_to_pickup_without_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=False, has_origin=True)
    assert ok is False


def test_can_start_en_route_to_pickup_with_driver_and_truck():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_cannot_go_en_route_without_origin():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=False)
    assert ok is False
    assert "origin" in reason.lower()


def test_cannot_reach_at_pickup_before_en_route_to_pickup():
    ok, reason = validate_transition("Export", "Ready to Dispatch", "At Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is False


def test_cannot_reach_at_delivery_before_en_route_to_delivery():
    ok, reason = validate_transition("Import", "At Pickup", "At Delivery", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is False


def test_import_cannot_return_empty_before_at_delivery():
    ok, reason = validate_transition("Import", "En Route to Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False
    assert "at delivery" in reason.lower()


def test_import_can_return_empty_after_at_delivery():
    ok, reason = validate_transition("Import", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_export_cannot_return_empty_at_all():
    ok, reason = validate_transition("Export", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_import_complete_requires_returning_empty_when_required():
    ok, reason = validate_transition("Import", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True, empty_return_required=True)
    assert ok is False


def test_import_complete_ok_from_at_delivery_when_not_required():
    ok, reason = validate_transition("Import", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True, empty_return_required=False)
    assert ok is True


def test_export_complete_ok_from_at_delivery():
    ok, reason = validate_transition("Export", "At Delivery", "Completed", has_driver=True, has_truck=True, has_origin=True)
    assert ok is True


def test_completed_load_blocks_further_transitions():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_completed_load_allows_transition_with_override():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is True


def test_cancel_allowed_from_active_status():
    ok, reason = validate_transition("Import", "En Route to Pickup", CANCELLED_STATUS)
    assert ok is True


def test_cannot_cancel_a_completed_load():
    ok, reason = validate_transition("Import", COMPLETION_STATUS, CANCELLED_STATUS)
    assert ok is False


def test_backward_transition_blocked_without_override():
    ok, reason = validate_transition("Import", "At Pickup", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False


def test_backward_transition_allowed_with_override():
    ok, reason = validate_transition("Import", "At Pickup", "En Route to Pickup", has_driver=True, has_truck=True, has_origin=True, override=True)
    assert ok is True


def test_unknown_new_status_rejected():
    ok, reason = validate_transition("Import", "Ready to Dispatch", "Not A Real Status")
    assert ok is False


def test_returning_empty_rejected_as_target_for_export():
    ok, reason = validate_transition("Export", "At Delivery", "Returning Empty", has_driver=True, has_truck=True, has_origin=True)
    assert ok is False
