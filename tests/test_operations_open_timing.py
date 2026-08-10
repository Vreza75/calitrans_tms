from __future__ import annotations

import logging

from services import operations_open_timing


def test_work_item_open_timing_records_stage_and_total(monkeypatch) -> None:
    ticks = iter([10.0, 10.3, 10.5])
    monkeypatch.setattr(operations_open_timing.time, "perf_counter", lambda: next(ticks))
    state: dict[str, object] = {}
    trace = operations_open_timing.WorkItemOpenTiming(
        work_item_id=42,
        started_at=10.0,
    )

    with trace.stage("work_item_database_fetch"):
        pass
    snapshot = trace.finish(
        state=state,
        logger=logging.getLogger("test.operations_open_timing"),
    )

    assert snapshot["work_item_id"] == 42
    assert snapshot["stages_ms"]["work_item_database_fetch"] == 300.0
    assert snapshot["total_ms"] == 500.0
    assert state["operations_open_timing_last"] == snapshot


def test_timing_snapshot_contains_no_message_or_attachment_content() -> None:
    state: dict[str, object] = {}
    trace = operations_open_timing.WorkItemOpenTiming(work_item_id=7)
    snapshot = trace.finish(
        state=state,
        logger=logging.getLogger("test.operations_open_timing"),
    )

    assert set(snapshot) == {"work_item_id", "total_ms", "stages_ms"}
