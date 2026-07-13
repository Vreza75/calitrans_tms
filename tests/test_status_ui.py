from services.dispatch_stages import SHARED_STAGES
from services.dispatch_workflow_service import STATUS_COLORS, get_status_ui


def test_every_shared_stage_has_a_status_color():
    for stage in SHARED_STAGES:
        assert stage in STATUS_COLORS, f"{stage!r} missing from STATUS_COLORS"


def test_get_status_ui_returns_background_border_text_for_known_status():
    ui = get_status_ui("Ready to Dispatch")
    assert ui["background"]
    assert ui["border"]
    assert ui["text"]


def test_get_status_ui_returns_safe_default_for_unknown_status():
    ui = get_status_ui("Not A Real Status")
    assert ui["background"]
    assert ui["border"]
    assert ui["text"]


def test_get_status_ui_matches_legacy_status_colors_dict():
    assert get_status_ui("Ready to Dispatch")["background"] == STATUS_COLORS["Ready to Dispatch"]


def test_completed_and_at_delivery_and_en_route_to_delivery_have_distinct_colors():
    completed = get_status_ui("Completed")["background"]
    at_delivery = get_status_ui("At Delivery")["background"]
    en_route = get_status_ui("En Route to Delivery")["background"]
    assert len({completed, at_delivery, en_route}) == 3
