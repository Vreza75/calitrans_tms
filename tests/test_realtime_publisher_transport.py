"""Phase 9: realtime/publisher.py tests - the transport-facing
BroadcastPublisher implementations and the get_publisher() factory (STEP
23: no silent no-op fallback when REALTIME_ENABLED is true)."""
from __future__ import annotations

from unittest import mock

import pytest

from realtime.publisher import NoOpBroadcastPublisher, SupabaseBroadcastPublisher, get_publisher


def test_noop_publisher_always_succeeds_and_makes_no_network_call():
    publisher = NoOpBroadcastPublisher()
    ok, error = publisher.broadcast(channel="loads", event_type="load.updated", payload={})
    assert ok is True
    assert error == ""


def test_get_publisher_defaults_to_noop_when_realtime_disabled():
    with mock.patch("config.get_secret", side_effect=lambda name, default=None: {"REALTIME_ENABLED": "false"}.get(name, default)):
        publisher = get_publisher()
    assert isinstance(publisher, NoOpBroadcastPublisher)


def test_get_publisher_defaults_to_noop_when_realtime_unset():
    with mock.patch("config.get_secret", side_effect=lambda name, default=None: default):
        publisher = get_publisher()
    assert isinstance(publisher, NoOpBroadcastPublisher)


def test_get_publisher_raises_loudly_when_enabled_but_unconfigured():
    """STEP 23: never silently fall back to no-op when realtime is
    supposed to be live - a misconfiguration must be loud, not a quiet
    broadcast black hole."""

    def _secret(name, default=None):
        return {"REALTIME_ENABLED": "true"}.get(name, default)

    with mock.patch("config.get_secret", side_effect=_secret):
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            get_publisher()


def test_get_publisher_returns_supabase_publisher_when_fully_configured():
    def _secret(name, default=None):
        return {
            "REALTIME_ENABLED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-key",
        }.get(name, default)

    with mock.patch("config.get_secret", side_effect=_secret):
        publisher = get_publisher()
    assert isinstance(publisher, SupabaseBroadcastPublisher)


def test_supabase_publisher_posts_the_expected_broadcast_shape():
    publisher = SupabaseBroadcastPublisher(base_url="https://example.supabase.co", service_role_key="fake-key")

    fake_response = mock.MagicMock(status_code=200)
    with mock.patch("requests.post", return_value=fake_response) as post:
        ok, error = publisher.broadcast(channel="loads", event_type="load.updated", payload={"load_id": 1})

    assert ok is True
    assert error == ""
    post.assert_called_once()
    assert post.call_args.args[0] == "https://example.supabase.co/realtime/v1/api/broadcast"
    body = post.call_args.kwargs["json"]
    assert body["messages"][0]["topic"] == "loads"
    assert body["messages"][0]["event"] == "load.updated"
    assert body["messages"][0]["payload"] == {"load_id": 1}
    headers = post.call_args.kwargs["headers"]
    assert headers["apikey"] == "fake-key"
    assert headers["Authorization"] == "Bearer fake-key"


def test_supabase_publisher_http_error_status_is_a_failure_not_an_exception():
    publisher = SupabaseBroadcastPublisher(base_url="https://example.supabase.co", service_role_key="fake-key")

    fake_response = mock.MagicMock(status_code=500)
    with mock.patch("requests.post", return_value=fake_response):
        ok, error = publisher.broadcast(channel="loads", event_type="load.updated", payload={})

    assert ok is False
    assert "500" in error


def test_supabase_publisher_network_exception_is_caught_and_returned_as_failure():
    import requests

    publisher = SupabaseBroadcastPublisher(base_url="https://example.supabase.co", service_role_key="fake-key")

    with mock.patch("requests.post", side_effect=requests.ConnectionError("no route to host")):
        ok, error = publisher.broadcast(channel="loads", event_type="load.updated", payload={})

    assert ok is False
    assert "no route to host" in error
