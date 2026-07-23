"""Permanent regression test for CASE-001 - New Import, Single Container,
Email Body Only. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a
scratch database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import run_case

CASE_ID = "CASE-001"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_001_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_001_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_001_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"] == "New Booking"
    assert first.actual["service_flow"] == second.actual["service_flow"] == "Import"
