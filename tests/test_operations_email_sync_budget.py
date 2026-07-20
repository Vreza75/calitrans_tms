"""Regression tests for the sync_operations_email_engine time-budget bug:
ensure_operations_email_sync_schema() (~26s on a cold session) and
email_client.fetch_operations_email_sync() together could consume the
entire interactive time budget before the per-message insert loop ever ran,
so every sync attempt imported zero messages and "Last Sync" never advanced.
These two pure helpers are what sync_operations_email_engine uses to keep
schema/fetch overrun from starving the insert loop of all its time.
"""
from services.operations_inbox_service import (
    compute_fetch_time_budget,
    compute_insert_loop_deadline,
)


def test_fetch_budget_reserves_time_for_insert_loop_under_normal_conditions():
    assert compute_fetch_time_budget(25, elapsed_before_fetch=5, insert_reserve_seconds=5) == 15


def test_fetch_budget_floors_at_minimum_after_slow_schema_check():
    assert compute_fetch_time_budget(25, elapsed_before_fetch=26, insert_reserve_seconds=5) == 5


def test_fetch_budget_floors_at_minimum_when_reserve_alone_exceeds_remaining():
    assert compute_fetch_time_budget(25, elapsed_before_fetch=10, insert_reserve_seconds=20) == 5


def test_insert_loop_deadline_guarantees_minimum_window_even_if_outer_deadline_passed():
    now = 1000.0
    outer_deadline = 990.0
    assert compute_insert_loop_deadline(outer_deadline, now, min_seconds=5) == 1005.0


def test_insert_loop_deadline_keeps_outer_deadline_when_ample_time_remains():
    now = 1000.0
    outer_deadline = 1020.0
    assert compute_insert_loop_deadline(outer_deadline, now, min_seconds=5) == 1020.0
