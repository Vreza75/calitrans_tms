from services.dispatch_board_view import (
    SHARED_BOARD_STAGES,
    get_board_columns,
    is_active_dispatch_status,
    to_shared_stage,
)


def test_shared_board_stages_exact_list():
    assert SHARED_BOARD_STAGES == [
        "Ready to Dispatch",
        "Assigned",
        "En Route to Pickup",
        "At Pickup",
        "En Route to Delivery",
        "At Delivery",
        "Empty Return",
        "Completed",
    ]


def test_to_shared_stage_import_en_route_and_at_port():
    assert to_shared_stage("Import", "En Route to Port") == "En Route to Pickup"
    assert to_shared_stage("Import", "At Port") == "At Pickup"
    assert to_shared_stage("Import", "Container Picked Up") == "At Pickup"
    assert to_shared_stage("Import", "Returning Empty") == "Empty Return"
    assert to_shared_stage("Import", "Dispatch Complete") == "Completed"


def test_to_shared_stage_export_in_gated_maps_to_completed():
    assert to_shared_stage("Export", "In-Gated") == "Completed"
    assert to_shared_stage("Export", "At Port") == "At Delivery"


def test_to_shared_stage_local_import_and_export_share_mapping():
    assert to_shared_stage("Local Import", "At Origin Warehouse") == "At Pickup"
    assert to_shared_stage("Local Export", "At Origin Warehouse") == "At Pickup"
    assert to_shared_stage("Local Import", "Delivered") == "At Delivery"


def test_to_shared_stage_unknown_status_returns_empty_string():
    assert to_shared_stage("Import", "Not A Real Status") == ""


def test_get_board_columns_all_returns_shared_stages():
    assert get_board_columns("All") == SHARED_BOARD_STAGES


def test_get_board_columns_specific_flow_returns_operational_stages():
    columns = get_board_columns("Export")
    assert columns[0] == "Ready to Dispatch"
    assert "In-Gated" in columns
    assert "Empty Return" not in columns


def test_is_active_dispatch_status_true_for_ready_to_dispatch_and_later():
    assert is_active_dispatch_status("Import", "Ready to Dispatch") is True
    assert is_active_dispatch_status("Import", "At Port") is True


def test_is_active_dispatch_status_false_for_pre_dispatch():
    assert is_active_dispatch_status("Import", "Booking Verified") is False
    assert is_active_dispatch_status("Import", "New") is False


def test_is_active_dispatch_status_false_for_dispatch_complete():
    assert is_active_dispatch_status("Import", "Dispatch Complete") is False
