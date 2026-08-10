"""Permanent regression test for CASE-009 - Delivery Address Change After
Driver Assignment. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points
at a scratch database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import require_scratch_database_url, run_case, scratch_database

CASE_ID = "CASE-009"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_009_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_009_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_009_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"] == "Booking Update"


def test_case_009_does_not_misread_driver_mention_as_a_driver_issue():
    """Locks in the DRIVER_PORT_TERMS narrowing: an address-change email
    that merely asks the driver to receive updated instructions must not
    be misclassified as an operational Driver Issue."""
    report = run_case(CASE_ID)
    assert report.actual["intent"] == "Booking Update"
    assert report.actual["intent"] != "Driver Issue"


def test_case_009_does_not_auto_overwrite_the_assigned_loads_address():
    """Locks in the case's hard rule: a proposed address change on a load
    that already has a driver assigned must never be silently applied -
    it requires dispatcher approval first."""
    report = run_case(CASE_ID)
    assert report.actual["existing_load_match"] == 1
    assert report.actual["delivery"]["address"] == "16200 North Freeway, Houston, TX 77090"

    url = require_scratch_database_url()
    with scratch_database(url):
        import db_client

        loads_df = db_client.read_df(
            "select address, driver_name, status from loads where booking_number = 'GCR-IMP-260801'"
        )
    assert len(loads_df) == 1
    row = loads_df.iloc[0]
    # Old address and driver assignment must still be in place.
    assert row["address"] == "4100 Market Center Drive, Houston, TX 77020"
    assert row["driver_name"] == "Mike Torres"
    assert row["status"] == "Driver Assigned"
