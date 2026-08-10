"""Tests for the pure sequencing logic behind multi-container child-load
creation. The DB-touching half of create_container_work_orders isn't
exercised here (no test database in this environment) - this covers the
part that decides *which* sequences still need a load, which is what
guarantees idempotency on a second click/rerun.
"""
from services.operations_multi_container_service import pending_container_sequences


def test_four_container_booking_needs_sequences_one_through_four():
    assert pending_container_sequences(4, 0) == [1, 2, 3, 4]


def test_second_call_after_two_created_only_asks_for_remaining_two():
    assert pending_container_sequences(4, 2) == [3, 4]


def test_fully_created_booking_needs_nothing_more():
    assert pending_container_sequences(4, 4) == []


def test_over_created_is_treated_as_nothing_remaining():
    assert pending_container_sequences(4, 5) == []


def test_single_container_booking_needs_one_sequence():
    assert pending_container_sequences(1, 0) == [1]
