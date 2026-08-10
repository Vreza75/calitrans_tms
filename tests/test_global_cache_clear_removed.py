from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files known to have carried a live st.cache_data.clear() call before this
# batch - operations_inbox_service.py's own targeted-invalidation coverage
# lives in tests/test_operations_inbox_cache_invalidation.py, not here.
CHECKED_FILES = [
    "admin_pages.py",
    "pages_app/admin.py",
    "pages_app/port_houston_integration.py",
    "pages_app/documents.py",
    "pages_app/orders_management.py",
    "pages_app/dispatch_board.py",
    "services/order_intake.py",
    "services/tms_data_service.py",
]


def test_no_live_global_cache_clear_calls_remain() -> None:
    """A bare st.cache_data.clear() wipes every @cache_data/@ttl_cache
    function app-wide - Operations Inbox, cases, attachments, TMS loads -
    regardless of what the calling page actually mutated. Every call site
    in these files was replaced with either a targeted refresh (delegating
    to tms_data_service.refresh_data() when the mutation touched loads) or
    removed entirely (when nothing in the app caches the mutated table)."""
    offenders = []
    for relative_path in CHECKED_FILES:
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        live_calls = [
            line for line in source.splitlines() if line.strip() == "st.cache_data.clear()"
        ]
        if live_calls:
            offenders.append(relative_path)

    assert offenders == []
