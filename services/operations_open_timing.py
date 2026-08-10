from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator, MutableMapping


class WorkItemOpenTiming:
    """Collect lightweight, content-free work-item opening timings."""

    def __init__(
        self,
        *,
        work_item_id: int,
        started_at: float | None = None,
    ) -> None:
        self.work_item_id = int(work_item_id)
        self.started_at = started_at if started_at is not None else time.perf_counter()
        self.stages_ms: dict[str, float] = {}

    def mark_elapsed(self, name: str) -> None:
        self.stages_ms[name] = round(
            (time.perf_counter() - self.started_at) * 1000,
            2,
        )

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        stage_started = time.perf_counter()
        try:
            yield
        finally:
            self.stages_ms[name] = round(
                (time.perf_counter() - stage_started) * 1000,
                2,
            )

    def finish(
        self,
        *,
        state: MutableMapping[str, Any],
        logger: logging.Logger,
    ) -> dict[str, Any]:
        snapshot = {
            "work_item_id": self.work_item_id,
            "total_ms": round((time.perf_counter() - self.started_at) * 1000, 2),
            "stages_ms": dict(self.stages_ms),
        }
        state["operations_open_timing_last"] = snapshot
        logger.info(
            "operations work item open timing",
            extra={
                "work_item_id": self.work_item_id,
                "total_ms": snapshot["total_ms"],
                "stage_timings_ms": snapshot["stages_ms"],
            },
        )
        return snapshot
