"""column_exists() lets the ensure_*_schema() functions skip ~50 idempotent
DDL round trips (~26-45s total) once the schema is already applied, instead
of relying only on an st.session_state flag that doesn't reliably survive
across reruns/process restarts - that flag falling through was silently
eating the entire interactive sync time budget on every "cold" attempt.
"""
import os

import pytest

import db_client
from db_client import column_exists

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


def test_known_column_reports_true():
    assert column_exists("order_intake", "id") is True


def test_unknown_column_reports_false():
    assert column_exists("order_intake", "definitely_not_a_real_column_xyz") is False


def test_unknown_table_reports_false():
    assert column_exists("definitely_not_a_real_table_xyz", "id") is False
