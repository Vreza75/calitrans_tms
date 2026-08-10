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


def test_send_message_rejects_missing_subject(monkeypatch):
    """Verify send_message() returns failure without calling _send_smtp_email when subject is missing."""
    call_tracker = []

    def _should_not_be_called(*a, **k):
        call_tracker.append(True)
        raise AssertionError("should not be called")

    monkeypatch.setattr(email_provider, "_send_smtp_email", _should_not_be_called)
    result = email_provider.send_message("customer@example.com", "Hello")
    assert result == {"success": False, "provider_message_id": None, "error": "subject is required for email"}
    assert not call_tracker, "_send_smtp_email should not have been called"


def test_send_message_rejects_whitespace_only_subject(monkeypatch):
    """Verify send_message() returns failure without calling _send_smtp_email when subject is whitespace-only."""
    call_tracker = []

    def _should_not_be_called(*a, **k):
        call_tracker.append(True)
        raise AssertionError("should not be called")

    monkeypatch.setattr(email_provider, "_send_smtp_email", _should_not_be_called)
    result = email_provider.send_message("customer@example.com", "Hello", subject="   ")
    assert result == {"success": False, "provider_message_id": None, "error": "subject is required for email"}
    assert not call_tracker, "_send_smtp_email should not have been called"
