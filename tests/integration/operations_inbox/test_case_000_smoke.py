"""Permanent regression test for CASE-000 - the certification harness smoke
fixture. Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a scratch
database (same opt-in gate as tests/test_migration_runner.py).

This is the template every real case (CASE-001, CASE-002, ...) copies once it
reaches ACCEPTED status: change CASE_ID, copy the function body verbatim.
"""
import os

import pytest

from tests.integration.operations_inbox.harness import run_case

CASE_ID = "CASE-000"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_smoke_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_smoke_rerun_creates_no_duplicates():
    # run_case already reprocesses the same email once internally; this test
    # locks in that the duplicate-protection result itself is PASS.
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_smoke_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"]
    assert first.actual["service_flow"] == second.actual["service_flow"]
