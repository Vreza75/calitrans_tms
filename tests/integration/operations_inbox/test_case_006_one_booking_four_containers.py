"""Permanent regression test for CASE-006 - One Booking with Four
Containers (RICGX1235800 / Continental Industries Group). Skipped unless
INBOX_CERTIFICATION_DATABASE_URL points at a scratch database (same
opt-in gate as tests/test_migration_runner.py).

Scope note (see case.yaml): the booking-level pending draft
(order_intake_drafts row) is only ever created by a dispatcher clicking a
button in pages_app/operations_inbox.py, never by the automated
sync_operations_email_engine pipeline this harness drives. So this file
certifies two things separately:
  1. Automated intake correctly parses container_qty=4/size=40HC and keeps
     New Booking classification despite passive BOL/invoice language.
  2. create_container_work_orders (the dispatcher-confirmed child-load
     creation function) is idempotent and creates exactly 4 correctly
     sequenced child loads - called directly, simulating dispatcher
     confirmation the same way CASE-001..005 simulate order approval via
     create_load_from_inbox_item.
"""
import os

import pytest

from tests.integration.operations_inbox.harness import (
    require_scratch_database_url,
    run_case,
    scratch_database,
)

CASE_ID = "CASE-006"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INBOX_CERTIFICATION_DATABASE_URL"),
    reason=(
        "Requires INBOX_CERTIFICATION_DATABASE_URL pointing at an empty, "
        "disposable PostgreSQL database. Never set this to the app's real "
        "DATABASE_URL."
    ),
)

DRAFT = {
    "service_flow": "Export",
    "booking_number": "RICGX1235800",
    "reference_number": "SO217089a/C25749C",
    "customer": "CONTINENTAL INDUSTRIES GROUP",
    "full_return_terminal": "Bayport Terminal",
    "empty_pickup_location": "CONGLOBAL-LA PORTE",
    "container_size": "40HC",
    "commodity": "RESIN NON HAZ",
}


def test_case_006_passes_clean():
    report = run_case(CASE_ID)
    assert report.comparison["exact_record_pass"], report.comparison["diffs"]


def test_case_006_rerun_creates_no_duplicates():
    report = run_case(CASE_ID)
    assert report.duplicate_protection == "PASS"
    assert report.row_count_after_rerun == report.row_count_first_run


def test_case_006_is_deterministic_across_independent_runs():
    first = run_case(CASE_ID)
    second = run_case(CASE_ID)
    assert first.comparison["exact_record_pass"] == second.comparison["exact_record_pass"]
    assert first.actual["intent"] == second.actual["intent"] == "New Booking"
    assert first.actual["container_count"] == second.actual["container_count"] == 4


def test_case_006_quantity_is_not_read_as_a_container_number():
    """Locks in the case's hard rule: the stated quantity (4) must never be
    treated as a physical container number, and no container number should
    be invented before the carrier issues one."""
    report = run_case(CASE_ID)
    assert report.actual["container_count"] == 4
    assert report.actual["containers"] == []
    assert report.actual["booking_number"] == "RICGX1235800"


def test_case_006_child_load_creation_is_idempotent_and_creates_exactly_four():
    report = run_case(CASE_ID)
    intake_id = report.actual["_row_ids"][0]
    url = require_scratch_database_url()

    with scratch_database(url):
        import db_client
        from services.operations_multi_container_service import create_container_work_orders

        # Clean slate for this sub-test: no loads yet for this booking.
        db_client.execute(
            "delete from loads where parent_booking_key = :key",
            {"key": "RICGX1235800"},
        )

        first = create_container_work_orders(
            intake_id=intake_id,
            draft=DRAFT,
            conversation_key="RICGX1235800",
            subject="FCL Sea Freight Booking Confirmation - RICGX1235800",
            sender="bookings@fleurdelis.example.com",
            container_qty=4,
            containers_created=0,
        )
        assert sorted(first["created_load_ids"]) == first["created_load_ids"]
        assert len(first["created_load_ids"]) == 4
        assert first["errors"] == []
        assert first["containers_created"] == 4

        loads_df = db_client.read_df(
            """
            select container_sequence, container_total, is_placeholder_container, container_number, status
            from loads
            where parent_booking_key = :key
            order by container_sequence
            """,
            {"key": "RICGX1235800"},
        )
        assert list(loads_df["container_sequence"]) == [1, 2, 3, 4]
        assert (loads_df["container_total"] == 4).all()
        assert loads_df["is_placeholder_container"].all()
        assert loads_df["container_number"].isna().all()

        second = create_container_work_orders(
            intake_id=intake_id,
            draft=DRAFT,
            conversation_key="RICGX1235800",
            subject="FCL Sea Freight Booking Confirmation - RICGX1235800",
            sender="bookings@fleurdelis.example.com",
            container_qty=4,
            containers_created=first["containers_created"],
        )
        assert second["created_load_ids"] == []
        assert second["errors"] == []
        assert second["containers_created"] == 4

        total_df = db_client.read_df(
            "select count(*)::int as total from loads where parent_booking_key = :key",
            {"key": "RICGX1235800"},
        )
        assert total_df.iloc[0]["total"] == 4

        # Cleanup so other tests/reruns of this file start from a clean slate.
        db_client.execute(
            "delete from loads where parent_booking_key = :key",
            {"key": "RICGX1235800"},
        )
