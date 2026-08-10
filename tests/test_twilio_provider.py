from services.communications import twilio_provider


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_send_message_invalid_phone_returns_failure():
    result = twilio_provider.send_message("not a phone", "hello")
    assert result["success"] is False
    assert "not a valid phone number" in result["error"]


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "format_phone_e164", lambda p: "+18325551234")
    monkeypatch.setattr(twilio_provider, "send_sms", lambda phone, body: (True, "SM123"))
    result = twilio_provider.send_message("8325551234", "hello")
    assert result == {"success": True, "provider_message_id": "SM123", "error": None}


def test_send_message_failure(monkeypatch):
    monkeypatch.setattr(twilio_provider, "format_phone_e164", lambda p: "+18325551234")
    monkeypatch.setattr(twilio_provider, "send_sms", lambda phone, body: (False, "Twilio error (500): boom"))
    result = twilio_provider.send_message("8325551234", "hello")
    assert result["success"] is False
    assert result["error"] == "Twilio error (500): boom"


def test_get_status_missing_secrets_returns_unknown(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: None)
    assert twilio_provider.get_status("SM123") == "unknown"


def test_get_status_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: "fake_value")
    monkeypatch.setattr(twilio_provider.requests, "get", lambda *a, **k: _FakeResponse(200, {"status": "delivered"}))
    assert twilio_provider.get_status("SM123") == "delivered"


def test_get_delivery_receipts_missing_secrets_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: None)
    assert twilio_provider.get_delivery_receipts("SM123") == {}


def test_get_delivery_receipts_success(monkeypatch):
    monkeypatch.setattr(twilio_provider, "get_secret", lambda name, default=None: "fake_value")
    payload = {"status": "delivered", "error_code": None, "error_message": None, "date_sent": "2026-07-16"}
    monkeypatch.setattr(twilio_provider.requests, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = twilio_provider.get_delivery_receipts("SM123")
    assert result["status"] == "delivered"
    assert result["date_sent"] == "2026-07-16"
