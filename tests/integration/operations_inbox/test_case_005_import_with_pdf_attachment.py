"""Permanent regression test for CASE-005 - New Import with PDF Attachment.
Skipped unless INBOX_CERTIFICATION_DATABASE_URL points at a scratch
database (same opt-in gate as tests/test_migration_runner.py).
"""
import os
from pathlib import Path

import pytest

from tests.integration.operations_inbox.harness import require_scratch_database_url, run_case, scratch_database

CASE_ID = "CASE-005"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)


def test_case_005_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_005_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_005_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"] == "New Booking"
    assert first.actual["booking_number"] == second.actual["booking_number"] == "CAG-IMP-260805"


def test_case_005_pdf_fields_win_over_weak_email_body_guesses():
    """Locks in the attachment-precedence fix: the email body alone (short
    instruction only) would produce Customer="Example" (from the sender's
    example.com domain) and Booking Number="DOCUMENT" (misread from the
    subject) - the PDF's explicit labeled fields must win."""
    report = run_case(CASE_ID)
    assert report.actual["customer"] == "Coastal Appliance Group"
    assert report.actual["booking_number"] == "CAG-IMP-260805"
    # ... but sender identity must still win for contact fields - the PDF's
    # signature-scan produces a wrong contact name, so this must not "win".
    assert report.actual["references"]["contact_name"] == "Dana Phillips"


def test_case_005_attachment_is_physically_stored():
    report = run_case(CASE_ID)
    url = require_scratch_database_url()

    with scratch_database(url):
        import db_client

        df = db_client.read_df(
            "select file_path from order_intake where id = :row_id",
            {"row_id": report.actual["_row_ids"][0]},
        )
    file_path = df.iloc[0]["file_path"]
    assert file_path
    assert Path(file_path).is_file()
