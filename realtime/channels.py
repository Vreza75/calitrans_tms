# realtime/channels.py

from __future__ import annotations

"""Phase 9 STEP 15/16: channel naming and payload-safety guard.

Channel design (STEP 15's recommended initial pattern):
  - one COLLECTION channel per aggregate type, for list-view invalidation
    ("loads", "inbox", "communications", "documents")
  - one RESOURCE channel for the aggregate type a client is most likely
    to have an open detail workspace for today - "load:{id}" only, this
    pass. Other aggregate types don't get a resource channel yet (no
    detail workspace consumes one) - see docs/architecture/
    REALTIME_EVENTS.md for why this isn't built out further this pass.

Channels are logical Supabase Realtime Broadcast topic names, not
persistent server resources - see realtime/publisher.py."""

_COLLECTION_CHANNEL_BY_AGGREGATE_TYPE: dict[str, str] = {
    "load": "loads",
    "order_intake_item": "inbox",
    "dispatch_message": "communications",
    "document": "documents",
}

# Aggregate types that also get a per-resource channel, e.g. "load:381".
_RESOURCE_CHANNEL_AGGREGATE_TYPES = {"load"}


def collection_channel(aggregate_type: str) -> str:
    """Raises KeyError for an aggregate_type with no known channel - a
    caller emitting an event for a type this module doesn't know about is
    a bug (a new aggregate type needs a deliberate channel-design
    decision, not a silent default)."""
    return _COLLECTION_CHANNEL_BY_AGGREGATE_TYPE[aggregate_type]


def resource_channel(aggregate_type: str, aggregate_id: str) -> str | None:
    """None means "no resource-level channel for this aggregate type yet" -
    callers must treat that as normal, not an error (STEP 15: not every
    aggregate type needs one)."""
    if aggregate_type not in _RESOURCE_CHANNEL_AGGREGATE_TYPES:
        return None
    return f"{aggregate_type}:{aggregate_id}"


def channels_for(aggregate_type: str, aggregate_id: str) -> list[str]:
    """Every channel one event should be broadcast to - the collection
    channel always, plus the resource channel when one exists."""
    channels = [collection_channel(aggregate_type)]
    resource = resource_channel(aggregate_type, aggregate_id)
    if resource:
        channels.append(resource)
    return channels


# STEP 16: broadcast payloads are invalidation-oriented, never a
# replacement for API authorization. This is a strict ALLOWLIST (not a
# denylist of "bad" words) - a new metadata key must be deliberately
# added here before any emitter may use it, so a future engineer adding a
# convenient-looking field (e.g. a customer email, a rate) is stopped at
# review time, not caught later by guessing at forbidden substrings.
ALLOWED_METADATA_KEYS = {
    "new_status",
    "old_status",
    "driver_name",
    "truck",
    "updated_fields",
    "request_type",
    "review_status",
    "dispatch_message_id",
    "delivery_status",
    "load_id",
    "document_type",
    "status",
    "outcome",
}


def assert_no_sensitive_metadata(payload: dict) -> None:
    """Defense in depth: raises ValueError if `payload` contains any key
    outside ALLOWED_METADATA_KEYS. Called by realtime/events.py::
    publish_event on every write, so a disallowed field is caught at the
    moment a command tries to emit it - not discovered later by an
    operator inspecting broadcast traffic."""
    disallowed = set(payload.keys()) - ALLOWED_METADATA_KEYS
    if disallowed:
        raise ValueError(
            f"Refusing to record a domain event with disallowed metadata keys: {sorted(disallowed)}. "
            f"Add the key to realtime.channels.ALLOWED_METADATA_KEYS only after confirming it is not "
            f"sensitive (no message bodies, customer details, rates, addresses, or phone numbers)."
        )
