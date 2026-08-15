"""Phase 9: realtime/channels.py tests - channel naming (STEP 15) and the
metadata allowlist guard (STEP 16)."""
from __future__ import annotations

import pytest

from realtime.channels import (
    ALLOWED_METADATA_KEYS,
    assert_no_sensitive_metadata,
    channels_for,
    collection_channel,
    resource_channel,
)


def test_collection_channel_for_known_aggregate_types():
    assert collection_channel("load") == "loads"
    assert collection_channel("order_intake_item") == "inbox"
    assert collection_channel("dispatch_message") == "communications"
    assert collection_channel("document") == "documents"


def test_collection_channel_raises_for_unknown_aggregate_type():
    with pytest.raises(KeyError):
        collection_channel("something_new")


def test_resource_channel_only_exists_for_load():
    assert resource_channel("load", "381") == "load:381"
    assert resource_channel("document", "42") is None
    assert resource_channel("dispatch_message", "5") is None
    assert resource_channel("order_intake_item", "9") is None


def test_channels_for_load_includes_both_collection_and_resource():
    assert channels_for("load", "381") == ["loads", "load:381"]


def test_channels_for_document_only_includes_collection():
    assert channels_for("document", "42") == ["documents"]


def test_assert_no_sensitive_metadata_allows_every_currently_allowlisted_key():
    # Every key any Phase 9 emitter actually uses must be pre-approved -
    # this test fails loudly if that set ever silently shrinks.
    assert_no_sensitive_metadata({key: "x" for key in ALLOWED_METADATA_KEYS})


def test_assert_no_sensitive_metadata_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="disallowed metadata keys"):
        assert_no_sensitive_metadata({"driver_phone": "+15551234567"})


def test_assert_no_sensitive_metadata_rejects_message_body_and_rate_fields():
    for key in ("message_body", "rate", "customer_email", "address"):
        with pytest.raises(ValueError):
            assert_no_sensitive_metadata({key: "x"})


def test_assert_no_sensitive_metadata_allows_empty_payload():
    assert_no_sensitive_metadata({})
