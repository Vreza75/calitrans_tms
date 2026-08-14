from __future__ import annotations

"""Phase 8: shared, framework-neutral pagination types for new read
models (loads, dispatch, drivers, ...).

Strategy: page/offset, not cursor. STEP 2's own guidance allows this
("if offset/page pagination is significantly simpler and sufficient for
lower-churn resources, it may be used deliberately") - at this business's
actual scale (~10-20 drivers, a handful of concurrent dispatchers, per
CLAUDE.md), cursor pagination's main advantage (stable page boundaries
under high concurrent-write churn) doesn't meaningfully apply, and
Phase 7's `application/work_items/models.py::WorkItemPage` already
proved page/offset pagination works fine in production for the Inbox
queue - introducing a second pagination *style* alongside it would cost
more (a whole new opaque-cursor abstraction, two conventions for a
future web client to learn) than it would buy here.

Deliberately NOT retrofitting `WorkItemPage`/`FilterMeta`/`SortMeta` to
use this shared generic - Phase 7's Inbox pagination is already correct
and tested (STEP 14: "If Phase 7 APIs are already correct: leave them
alone"); this module exists for Phase 8's new resources (loads, dispatch,
drivers) only, not as a mandatory refactor of what already works.
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def normalize_page_size(page_size: int | None) -> int:
    """Server-side bound, independent of whatever an API layer also
    validates - calling the application function directly (a test, a
    future script) gets the same cap a caller going through FastAPI
    would, per this repo's established double-bound convention (see
    application/loads/queries.py::list_loads's own docstring for the
    precedent this generalizes)."""
    if not page_size:
        return DEFAULT_PAGE_SIZE
    return max(min(int(page_size), MAX_PAGE_SIZE), 1)


def normalize_page(page: int | None) -> int:
    return max(int(page or 1), 1)


@dataclass(frozen=True)
class PageRequest:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    sort_by: str = ""
    sort_direction: str = "desc"


@dataclass(frozen=True)
class PageResult(Generic[T]):
    items: list[T] = field(default_factory=list)
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    total_items: int = 0
    total_pages: int = 1
    sort_by: str = ""
    sort_direction: str = "desc"
