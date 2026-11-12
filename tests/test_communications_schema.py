"""ensure_communications_schema() extends dispatch_messages with the
columns the Communications Engine's provider-agnostic layer needs. Hits
the real dev database, same as tests/test_db_client_column_exists.py —
column_exists() is a real round trip and the added columns are additive
and idempotent, so re-running this test (or the app) is always safe.
"""
import os

import pytest

import db_client
from db_client import column_exists
from services.dispatch_data_service import ensure_communications_schema

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Requires MIGRATION_TEST_DATABASE_URL pointing at a disposable PostgreSQL database.",
)


@pytest.fixture(autouse=True)
def _use_disposable_database(monkeypatch):
    monkeypatch.setattr(
        db_client,
        "get_secret",
        lambda name, default=None: MIGRATION_TEST_DATABASE_URL if name == "DATABASE_URL" else default,
    )

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
