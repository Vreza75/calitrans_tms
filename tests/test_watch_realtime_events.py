"""Phase 9: scripts/watch_realtime_events.py tests - the dev-only manual
acceptance-test subscriber. No live Supabase connection required: these
tests cover URL/message construction, frame parsing, and topic
resolution only (the actual websocket I/O is not exercised here)."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from scripts.watch_realtime_events import (
    aggregate_from_topic,
    format_event_line,
    handle_frame,
    heartbeat_message,
    join_message,
    main,
    topics_for,
    websocket_url,
)


def test_websocket_url_converts_https_to_wss():
    url = websocket_url("https://example.supabase.co", "anon-key-123")
    assert url == "wss://example.supabase.co/realtime/v1/websocket?apikey=anon-key-123&vsn=1.0.0"


def test_websocket_url_converts_http_to_ws():
    url = websocket_url("http://localhost:54321", "anon-key")
    assert url.startswith("ws://localhost:54321/realtime/v1/websocket")


def test_websocket_url_strips_trailing_slash():
    url = websocket_url("https://example.supabase.co/", "anon-key")
    assert "//realtime" not in url.replace("wss://", "")


def test_topics_for_load_with_id_includes_collection_and_resource_channel():
    assert topics_for("load", "381") == ["loads", "load:381"]


def test_topics_for_load_without_id_is_collection_channel_only():
    assert topics_for("load", None) == ["loads"]


def test_topics_for_document_never_has_a_resource_channel():
    assert topics_for("document", "42") == ["documents"]


def test_join_message_shape():
    msg = join_message("loads", "1")
    assert msg["topic"] == "realtime:loads"
    assert msg["event"] == "phx_join"
    assert msg["ref"] == "1"
    assert msg["payload"]["config"]["broadcast"]["self"] is True


def test_heartbeat_message_shape():
    msg = heartbeat_message("7")
    assert msg == {"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "7"}


def test_aggregate_from_topic_parses_resource_channel():
    assert aggregate_from_topic("load:381") == ("load", "381")


def test_aggregate_from_topic_returns_none_for_collection_channel():
    assert aggregate_from_topic("loads") == (None, None)


def test_format_event_line_reports_na_for_fields_never_sent_on_the_wire():
    line = format_event_line(topic="load:381", event_type="load.status_changed")
    assert "aggregate_type=load" in line
    assert "aggregate_id=381" in line
    assert "event_id=n/a" in line
    assert "version=n/a" in line
    assert "occurred_at=n/a" in line


def test_format_event_line_collection_channel_has_no_aggregate_id():
    line = format_event_line(topic="loads", event_type="load.updated")
    assert "aggregate_type=n/a" in line
    assert "aggregate_id=n/a" in line


def test_handle_frame_ignores_non_broadcast_protocol_frames():
    phx_reply = json.dumps({"topic": "realtime:loads", "event": "phx_reply", "payload": {"status": "ok"}, "ref": "1"})
    assert handle_frame(phx_reply) is None


def test_handle_frame_ignores_invalid_json():
    assert handle_frame("not json") is None


def test_handle_frame_formats_a_broadcast_frame():
    frame = json.dumps(
        {
            "topic": "realtime:load:381",
            "event": "broadcast",
            "payload": {"type": "broadcast", "event": "load.status_changed", "payload": {"new_status": "Dispatched"}},
            "ref": None,
        }
    )
    line = handle_frame(frame)
    assert line is not None
    assert "channel=load:381" in line
    assert "event_type=load.status_changed" in line
    # never prints the actual payload values
    assert "Dispatched" not in line


def test_handle_frame_never_leaks_payload_values_for_sensitive_looking_content():
    frame = json.dumps(
        {
            "topic": "realtime:loads",
            "event": "broadcast",
            "payload": {"type": "broadcast", "event": "load.updated", "payload": {"driver_name": "Jane Doe"}},
            "ref": None,
        }
    )
    line = handle_frame(frame)
    assert "Jane Doe" not in line


def test_main_fails_fast_when_supabase_url_missing():
    with mock.patch("config.get_secret", side_effect=lambda name, default=None: {"SUPABASE_ANON_KEY": "x"}.get(name, default)), \
         mock.patch("sys.argv", ["watch_realtime_events.py", "load"]):
        assert main() == 1


def test_main_fails_fast_when_anon_key_missing():
    with mock.patch(
        "config.get_secret",
        side_effect=lambda name, default=None: {"SUPABASE_URL": "https://example.supabase.co"}.get(name, default),
    ), mock.patch("sys.argv", ["watch_realtime_events.py", "load"]):
        assert main() == 1


def test_main_never_reads_service_role_key():
    seen_names = []

    def _secret(name, default=None):
        seen_names.append(name)
        return {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_ANON_KEY": "anon"}.get(name, default)

    with mock.patch("config.get_secret", side_effect=_secret), mock.patch("sys.argv", ["watch_realtime_events.py", "load"]), mock.patch(
        "scripts.watch_realtime_events.watch", new=lambda **kwargs: None
    ), mock.patch("scripts.watch_realtime_events.asyncio.run"):
        main()

    assert "SUPABASE_SERVICE_ROLE_KEY" not in seen_names
    assert "SUPABASE_SECRET_KEY" not in seen_names


def test_main_rejects_unknown_aggregate_type():
    with pytest.raises(SystemExit):
        with mock.patch("sys.argv", ["watch_realtime_events.py", "something_new"]):
            main()
