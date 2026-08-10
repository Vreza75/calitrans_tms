"""Permanent regression test for CASE-008 - Existing Order Delivery-Date
Change. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a
scratch database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import require_scratch_database_url, run_case, scratch_database

CASE_ID = "CASE-008"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_008_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_008_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_008_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"] == "Booking Update"
    assert first.actual["decision"] == second.actual["decision"] == "Update Existing Order"


def test_case_008_matches_existing_load_and_creates_no_new_load():
    """Locks in the case's hard rule: an existing-load match must not
    create a new load, and the proposed date must not be silently applied
    without dispatcher approval."""
    report = run_case(CASE_ID)
    assert report.actual["existing_load_match"] == 1
    assert report.actual["decision"] == "Update Existing Order"
    assert report.actual["dates"]["delivery_need_date"] == "August 6, 2026"

    url = require_scratch_database_url()
    with scratch_database(url):
        import db_client

        loads_df = db_client.read_df(
            "select delivery_need_date from loads where booking_number = 'GCR-IMP-260801'"
        )
    assert len(loads_df) == 1
    # Old value must still be in place - proposed date is not auto-applied.
    assert str(loads_df.iloc[0]["delivery_need_date"]) == "2026-08-04"
