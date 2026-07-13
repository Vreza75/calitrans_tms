from services.dispatch_board_view import (
    get_board_columns,
    get_display_label,
    get_next_action,
    is_active_dispatch_status,
)


def test_import_display_labels():
    assert get_display_label("Import", "En Route to Pickup") == "En Route to Port"
    assert get_display_label("Import", "At Pickup") == "At Port"
    assert get_display_label("Import", "At Delivery") == "At Delivery Warehouse"
    assert get_display_label("Import", "Completed") == "Completed"
    assert get_display_label("Import", "Completed", via_empty_return=True) == "Empty Returned"


def test_export_display_labels():
    assert get_display_label("Export", "En Route to Pickup") == "En Route to Pickup Warehouse"
    assert get_display_label("Export", "At Delivery") == "At Port"
    assert get_display_label("Export", "Completed") == "In-Gated"


def test_local_display_labels_use_origin_destination_wording():
    assert get_display_label("Local Import", "At Pickup") == "At Origin Warehouse"
    assert get_display_label("Local Export", "At Delivery") == "At Destination Warehouse"
    assert get_display_label("Local Import", "Completed") == "Completed"


def test_unmapped_status_falls_back_to_itself():
    assert get_display_label("Import", "Cancelled") == "Cancelled"


def test_get_board_columns_returns_shared_stages():
    columns = get_board_columns()
    assert columns[0] == "Ready to Dispatch"
    assert "Returning Empty" in columns
    assert columns[-1] == "Completed"


def test_is_active_dispatch_status_true_for_ready_and_later():
    assert is_active_dispatch_status("Import", "Ready to Dispatch") is True
    assert is_active_dispatch_status("Import", "At Pickup") is True


def test_is_active_dispatch_status_false_for_pre_dispatch_and_completed():
    assert is_active_dispatch_status("Import", "Booking Verified") is False
    assert is_active_dispatch_status("Import", "Completed") is False


def test_next_action_ready_to_dispatch_unassigned():
    assert get_next_action("Import", "Ready to Dispatch", has_driver=False) == ("Assign & Start", "En Route to Pickup")


def test_next_action_ready_to_dispatch_assigned():
    assert get_next_action("Import", "Ready to Dispatch", has_driver=True) == ("Start En Route", "En Route to Pickup")


def test_next_action_en_route_to_pickup():
    assert get_next_action("Import", "En Route to Pickup") == ("Mark Arrived", "At Pickup")


def test_next_action_at_pickup_import_vs_other():
    assert get_next_action("Import", "At Pickup") == ("Mark Container Picked Up", "En Route to Delivery")
    assert get_next_action("Export", "At Pickup") == ("Mark Loaded / Picked Up", "En Route to Delivery")


def test_next_action_at_delivery_import_with_and_without_empty_return():
    assert get_next_action("Import", "At Delivery", empty_return_required=True) == ("Start Empty Return", "Returning Empty")
    assert get_next_action("Import", "At Delivery", empty_return_required=False) == ("Complete Dispatch", "Completed")


def test_next_action_at_delivery_export_and_local():
    assert get_next_action("Export", "At Delivery") == ("Mark In-Gated", "Completed")
    assert get_next_action("Local Import", "At Delivery") == ("Mark Delivered", "Completed")


def test_next_action_returning_empty():
    assert get_next_action("Import", "Returning Empty") == ("Mark Empty Returned", "Completed")


def test_next_action_none_for_completed_and_cancelled():
    assert get_next_action("Import", "Completed") is None
    assert get_next_action("Import", "Cancelled") is None
