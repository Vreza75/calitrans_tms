"""Phase 7 closure (STEP 11/12): the "Sync Email Engine" button must
enqueue-and-return, not perform mailbox-wide fetch/parse/classify/match/
persist inline as part of normal page execution.

Transitional UX pass: Sync Email Engine / Refresh Inbox / Recheck Next
Batch were relocated from the normal dispatcher Operations Inbox view to
Admin/Diagnostics (pages_app/email_imports.py's "Manual Inbox Processing"
expander) - routine processing already runs automatically via the
scheduled worker, so these are troubleshooting-only controls now, not
part of daily dispatcher workflow. render_operations_inbox() itself must
no longer reference them at all.

Source-inspection style, same pattern as tests/test_global_cache_clear_
removed.py and tests/test_operations_order_draft_queue_attachments.py::
test_queue_render_uses_bordered_rows_and_stable_open_id - Streamlit
button click handlers have no dedicated test harness in this codebase,
so proving "this code path calls X, not Y" is done by scanning the
function's own source.
"""
from __future__ import annotations

import inspect

import pages_app.email_imports as email_imports
import pages_app.operations_inbox as operations_inbox


def test_sync_button_enqueues_via_request_inbox_sync_not_inline_processing():
    source = inspect.getsource(email_imports._render_manual_inbox_processing)

    assert "request_inbox_sync(actor=principal)" in source
    assert "ops.sync_operations_email_engine(" not in source


def test_sync_refresh_recheck_buttons_are_not_in_the_normal_dispatcher_view():
    source = inspect.getsource(operations_inbox.render_operations_inbox)

    assert '"Sync Email Engine"' not in source
    assert '"Refresh Inbox"' not in source
    assert '"Recheck Next Batch"' not in source
    assert "request_inbox_sync(" not in source
    assert "auto_classify_open_inbox_items(" not in source


def test_sync_operations_email_engine_is_still_imported_for_the_admin_debug_path():
    """Not removed - services.operations_inbox_service::
    sync_operations_email_engine remains available for
    pages_app/email_imports.py's admin debug "Sync Recent Mail" tool
    (immediate-feedback debugging, unconverted on purpose - see
    docs/architecture/WORKER_RUNTIME.md)."""
    import pages_app.email_imports as email_imports

    source = inspect.getsource(email_imports._render_operations_diagnostics)
    assert "ops.sync_operations_email_engine(" in source
