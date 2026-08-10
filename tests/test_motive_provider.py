from services.communications import motive_provider


def test_send_message_returns_not_configured_failure():
    result = motive_provider.send_message("driver-1", "hello")
    assert result["success"] is False
    assert result["provider_message_id"] is None
    assert "not yet configured" in result["error"]


def test_get_status_returns_unknown():
    assert motive_provider.get_status("anything") == "unknown"


def test_get_delivery_receipts_returns_empty_dict():
    assert motive_provider.get_delivery_receipts("anything") == {}
