"""Permanent regression test for CASE-010 - Two Separate Orders in One
Email. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a scratch
database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import run_case

CASE_ID = "CASE-010"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_010_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_010_creates_exactly_two_rows():
    report = run_case(CASE_ID)
    assert report.actual["_row_count"] == 2


def test_case_010_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run == 2


def test_case_010_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["order_numbers"] == second.actual["order_numbers"] == [
        "APEX-260810", "APEX-260811",
    ]


def test_case_010_preserves_both_bookings_and_both_containers():
    """Locks in the case's hard rule: neither order's Booking Number or
    Container Number may be dropped, and neither must bleed into the
    other's row."""
    report = run_case(CASE_ID)
    assert report.actual["order_numbers"] == ["APEX-260810", "APEX-260811"]
    assert report.actual["containers"] == ["HLXU3000001", "HLXU3000002"]
    assert report.actual["container_count"] == 2


def test_case_010_stays_in_new_orders_queue_not_review():
    """A clean split is a routine multi-order email, not a flagged
    problem - it must not be silently routed to the Review queue the way
    CASE-007's mismatch is."""
    report = run_case(CASE_ID)
    assert report.actual["queue"] == "New Orders"
    assert report.actual["decision"] == "Create New Order"
    assert report.actual["requires_human_review"] is True
