"""DEV-ONLY: manual acceptance-test subscriber for Phase 9 realtime.

Verifies that a Supabase Broadcast message sent by services/
realtime_publisher.py is actually received by a client, by opening a
real websocket subscription and printing what arrives. Not part of the
Phase 9 delivery pipeline itself - a standalone listener for manual
testing only. No Streamlit import.

Usage:
    python scripts/watch_realtime_events.py load            # collection channel "loads" only
    python scripts/watch_realtime_events.py load 381         # + resource channel "load:381"
    python scripts/watch_realtime_events.py inbox            # note: aggregate_type is "order_intake_item"
    python scripts/watch_realtime_events.py order_intake_item
    python scripts/watch_realtime_events.py dispatch_message
    python scripts/watch_realtime_events.py document

The aggregate_type argument is the same value emitters pass to
realtime/events.py::publish_event (e.g. "load", "order_intake_item",
"dispatch_message", "document") - NOT the channel name. Channels
themselves are never guessed here: this script calls realtime/
channels.py::collection_channel / channels_for directly, the exact same
functions services/realtime_publisher.py::process_one calls before
broadcasting, so it is structurally impossible for this tool to
subscribe to a channel the publisher wouldn't actually use.

Config (config.get_secret - same env/.env/Streamlit-secrets precedence
every other module in this codebase uses): requires SUPABASE_URL and
SUPABASE_ANON_KEY. Deliberately never reads SUPABASE_SERVICE_ROLE_KEY or
SUPABASE_SECRET_KEY - a client-side subscriber has no legitimate use for
a service-role credential, and this script must never be able to print
one even by accident. If SUPABASE_ANON_KEY is not yet configured for
this project, add it alongside the existing SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY entries.

Prints only safe invalidation metadata per received broadcast:
event_id, event_type, aggregate_type, aggregate_id, version,
occurred_at - never the raw business `metadata` dict or its values
(Phase 10 addition: services/realtime_publisher.py now wraps that
metadata in an envelope alongside these bookkeeping fields - see
services/realtime_publisher.py::_envelope_payload - so they are read
directly off the wire here rather than guessed from the topic name).

Requires the `websockets` package (already present transitively via the
uvicorn[standard] entry in requirements.txt - not added as a new
top-level dependency for this dev-only tool).

Protocol: implements the Supabase/Phoenix Channels websocket broadcast
subscription protocol directly (phx_join / broadcast / heartbeat) rather
than pulling in supabase-py's realtime client (websockets + gotrue +
postgrest + storage3) for a dev-only listener - same "smallest
production-appropriate mechanism" choice realtime/publisher.py made for
the send side. NOT verified against a live Supabase project in this
pass - same caveat as realtime/publisher.py::SupabaseBroadcastPublisher.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_HEARTBEAT_INTERVAL_SECONDS = 25
_KNOWN_AGGREGATE_TYPES = ("load", "order_intake_item", "dispatch_message", "document")


def websocket_url(base_url: str, anon_key: str) -> str:
    ws_base = base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/realtime/v1/websocket?apikey={anon_key}&vsn=1.0.0"


def topics_for(aggregate_type: str, aggregate_id: str | None) -> list[str]:
    """The exact channel(s) services/realtime_publisher.py would
    broadcast this aggregate_type/aggregate_id to - delegates entirely to
    realtime/channels.py, never reimplements the naming convention."""
    from realtime.channels import channels_for, collection_channel

    if aggregate_id:
        return channels_for(aggregate_type, aggregate_id)
    return [collection_channel(aggregate_type)]


def join_message(topic: str, ref: str) -> dict[str, Any]:
    return {
        "topic": f"realtime:{topic}",
        "event": "phx_join",
        "payload": {"config": {"broadcast": {"self": True, "ack": False}, "presence": {"key": ""}}},
        "ref": ref,
    }


def heartbeat_message(ref: str) -> dict[str, Any]:
    return {"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": ref}


def format_event_line(*, topic: str, event_type: str, envelope: dict[str, Any]) -> str:
    """`envelope` is the broadcast payload built by services/
    realtime_publisher.py::_envelope_payload - never pass its "metadata"
    key through to the printed line (that's the business payload this
    tool deliberately never displays)."""
    return (
        f"channel={topic} event_type={event_type} "
        f"aggregate_type={envelope.get('aggregate_type') or 'n/a'} aggregate_id={envelope.get('aggregate_id') or 'n/a'} "
        f"event_id={envelope.get('event_id', 'n/a')} version={envelope.get('version') or 'n/a'} "
        f"occurred_at={envelope.get('occurred_at') or 'n/a'}"
    )


def handle_frame(raw: str) -> str | None:
    """Parses one raw websocket text frame. Returns a formatted line for a
    broadcast event, or None for any other Phoenix protocol frame
    (phx_reply, presence_state, heartbeat ack, ...) - those are not
    domain events and must not be printed as if they were."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if message.get("event") != "broadcast":
        return None

    inner = message.get("payload") or {}
    event_type = inner.get("event", "unknown")
    envelope = inner.get("payload") or {}
    topic = str(message.get("topic", "")).removeprefix("realtime:")
    return format_event_line(topic=topic, event_type=event_type, envelope=envelope)


async def watch(*, url: str, topics: list[str]) -> None:
    import websockets

    ref_counter = 0

    def next_ref() -> str:
        nonlocal ref_counter
        ref_counter += 1
        return str(ref_counter)

    async with websockets.connect(url) as ws:
        for topic in topics:
            await ws.send(json.dumps(join_message(topic, next_ref())))
        print(f"Subscribed to: {', '.join(f'realtime:{t}' for t in topics)}")
        print("Waiting for broadcasts (Ctrl+C to stop)...")

        async def heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                await ws.send(json.dumps(heartbeat_message(next_ref())))

        heartbeat_task = asyncio.create_task(heartbeat_loop())
        try:
            async for raw in ws:
                line = handle_frame(raw)
                if line:
                    print(line)
        finally:
            heartbeat_task.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("aggregate_type", choices=_KNOWN_AGGREGATE_TYPES)
    parser.add_argument("aggregate_id", nargs="?", default=None)
    args = parser.parse_args()

    from config import get_secret

    base_url = get_secret("SUPABASE_URL")
    anon_key = get_secret("SUPABASE_ANON_KEY")
    if not base_url or not anon_key:
        print(
            "SUPABASE_URL and SUPABASE_ANON_KEY must both be configured (config.get_secret - "
            "env/.env/Streamlit-secrets precedence). This tool never reads SUPABASE_SERVICE_ROLE_KEY."
        )
        return 1

    topics = topics_for(args.aggregate_type, args.aggregate_id)
    url = websocket_url(base_url, anon_key)

    try:
        asyncio.run(watch(url=url, topics=topics))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
