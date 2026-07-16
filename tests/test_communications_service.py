import pandas as pd

from services.communications import communications_service as cs


def _fake_read_df(dispatch_rows, customer_rows):
    def _read_df(sql, params=None):
        if "dispatch_messages" in sql:
            return pd.DataFrame(dispatch_rows)
        if "load_communications" in sql:
            return pd.DataFrame(customer_rows)
        raise AssertionError(f"unexpected query: {sql}")

    return _read_df


def test_merges_both_sources_sorted_newest_first(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)
    dispatch_rows = [
        {
            "created_at": "2026-07-16 10:00:00",
            "direction": "outbound",
            "channel": "twilio",
            "party": "+18325551234",
            "message_body": "Dispatch text",
        },
    ]
    customer_rows = [
        {
            "created_at": "2026-07-16 11:00:00",
            "direction": "inbound",
            "channel": "email",
            "party": "customer@example.com",
            "message_body": "Reply",
        },
    ]
    monkeypatch.setattr(cs, "read_df", _fake_read_df(dispatch_rows, customer_rows))
    result = cs.get_load_timeline(123)
    assert list(result["message_body"]) == ["Reply", "Dispatch text"]


def test_both_sources_empty_returns_empty_df_with_expected_columns(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)
    monkeypatch.setattr(cs, "read_df", _fake_read_df([], []))
    result = cs.get_load_timeline(123)
    assert result.empty
    assert list(result.columns) == ["created_at", "direction", "channel", "party", "message_body"]


def test_dispatch_source_error_falls_back_to_customer_only(monkeypatch):
    monkeypatch.setattr(cs, "ensure_communications_schema", lambda: None)

    def _read_df(sql, params=None):
        if "dispatch_messages" in sql:
            raise RuntimeError("db down")
        return pd.DataFrame(
            [
                {
                    "created_at": "2026-07-16 11:00:00",
                    "direction": "inbound",
                    "channel": "email",
                    "party": "x",
                    "message_body": "y",
                }
            ]
        )

    monkeypatch.setattr(cs, "read_df", _read_df)
    result = cs.get_load_timeline(123)
    assert len(result) == 1
    assert result.iloc[0]["message_body"] == "y"


def test_routes_to_email_provider(monkeypatch):
    monkeypatch.setattr(
        cs.email_provider,
        "send_message",
        lambda r, b, **k: {"success": True, "provider_message_id": None, "error": None},
    )
    result = cs.send_message("email", "customer@example.com", "hi", subject="Update")
    assert result["success"] is True


def test_routes_to_twilio_provider(monkeypatch):
    monkeypatch.setattr(
        cs.twilio_provider,
        "send_message",
        lambda r, b, **k: {"success": True, "provider_message_id": "SM1", "error": None},
    )
    result = cs.send_message("twilio", "8325551234", "hi")
    assert result["provider_message_id"] == "SM1"


def test_unknown_channel_returns_failure_without_raising():
    result = cs.send_message("carrier_pigeon", "recipient", "hi")
    assert result["success"] is False
    assert "Unknown communications channel" in result["error"]
