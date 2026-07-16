from services.communications import email_provider


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(email_provider, "_send_smtp_email", lambda *a, **k: None)
    result = email_provider.send_message("customer@example.com", "Hello", subject="Update")
    assert result == {"success": True, "provider_message_id": None, "error": None}


def test_send_message_failure(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("Missing email settings.")

    monkeypatch.setattr(email_provider, "_send_smtp_email", _boom)
    result = email_provider.send_message("customer@example.com", "Hello", subject="Update")
    assert result["success"] is False
    assert "Missing email settings" in result["error"]


def test_get_status_returns_unknown():
    assert email_provider.get_status("anything") == "unknown"


def test_get_delivery_receipts_returns_empty_dict():
    assert email_provider.get_delivery_receipts("anything") == {}
