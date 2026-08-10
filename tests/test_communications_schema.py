"""ensure_communications_schema() extends dispatch_messages with the
columns the Communications Engine's provider-agnostic layer needs. Hits
the real dev database, same as tests/test_db_client_column_exists.py —
column_exists() is a real round trip and the added columns are additive
and idempotent, so re-running this test (or the app) is always safe.
"""
from db_client import column_exists
from services.dispatch_data_service import ensure_communications_schema

EXPECTED_COLUMNS = [
    "provider",
    "delivery_status",
    "read_status",
    "attachments",
    "metadata",
    "provider_message_id",
]


def test_ensure_communications_schema_adds_expected_columns():
    ensure_communications_schema()
    for column in EXPECTED_COLUMNS:
        assert column_exists("dispatch_messages", column) is True


def test_ensure_communications_schema_is_idempotent():
    ensure_communications_schema()
    ensure_communications_schema()
    assert column_exists("dispatch_messages", "provider") is True
