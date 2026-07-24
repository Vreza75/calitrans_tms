"""Permanent regression test for CASE-007 - Container Quantity Mismatch.
Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a scratch
database (same opt-in gate as tests/test_migration_runner.py).
"""
import os

import pytest

from tests.integration.operations_inbox.harness import run_case

CASE_ID = "CASE-007"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_007_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_007_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_007_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["decision"] == second.actual["decision"] == "Human Review Required"


def test_case_007_preserves_all_three_valid_container_numbers():
    """Locks in the case's hard rule: none of the listed container numbers
    may be dropped, and the missing fourth must never be invented."""
    report = run_case(CASE_ID)
    assert report.actual["container_count"] == 4
    assert report.actual["containers"] == ["TEMU2000001", "TEMU2000002", "TEMU2000003"]


def test_case_007_blocks_automatic_order_creation():
    report = run_case(CASE_ID)
    assert report.actual["decision"] == "Human Review Required"
    assert report.actual["requires_human_review"] is True
