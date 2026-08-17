"""Regression tests for Issue 2 (transitional Streamlit UX pass): Sync
Email Engine / Refresh Inbox / Recheck Next Batch were relocated from the
normal Operations Inbox dispatcher view to Admin/Diagnostics
(pages_app/email_imports.py's "Manual Inbox Processing" expander).

Covers what tests/test_operations_inbox_sync_button_async.py and
tests/test_operations_inbox_ui_cleanup.py don't: role protection for
Recheck Next Batch (services.operations_inbox_service.
auto_classify_open_inbox_items has no application-command layer of its
own, so page visibility alone would otherwise be its only gate - see
_render_manual_inbox_processing's docstring), and the action-button
column width fix (the "Open" label wrapping bug).
"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_SOURCE = (ROOT / "pages_app" / "email_imports.py").read_text(encoding="utf-8")
INBOX_SOURCE = (ROOT / "pages_app" / "operations_inbox.py").read_text(encoding="utf-8")

_RECHECK_SCRIPT = """
import streamlit as st
from application.auth.models import AuthenticatedActor, Role
from pages_app.email_imports import _render_manual_inbox_processing

principal = AuthenticatedActor(actor="{actor_email}", role=Role.{role})
_render_manual_inbox_processing(principal)
"""


def test_recheck_next_batch_is_denied_for_a_role_without_work_item_manage():
    """ACCOUNTING has no Permission.WORK_ITEM_MANAGE (see
    application/auth/permissions.py's ROLE_PERMISSIONS) - clicking Recheck
    Next Batch must show an explicit error and never reach the service
    call, not just be hidden by section-level routing."""
    at = AppTest.from_string(
        _RECHECK_SCRIPT.format(actor_email="accounting@calitranscorp.com", role="ACCOUNTING")
    )
    at.run(timeout=15)
    assert not at.exception

    recheck_btn = next(b for b in at.button if b.label == "Recheck Next Batch")
    recheck_btn.click().run(timeout=15)

    assert not at.exception
    assert len(at.error) == 1
    assert "permission" in at.error[0].value.lower() or "authoriz" in at.error[0].value.lower()


def test_manual_inbox_processing_gates_recheck_with_require_permission():
    assert "require_permission(principal, Permission.WORK_ITEM_MANAGE)" in ADMIN_SOURCE


def test_action_column_is_wide_enough_to_avoid_open_label_wrap():
    """Regression guard for the reported 'O/p/e/n' vertical-wrap bug -
    the action column's width fraction must be meaningfully larger than
    the old 0.65, not just present."""
    assert "[width for _, _, width in queue_columns] + [0.65]" not in INBOX_SOURCE
    assert "[width for _, _, width in queue_columns] + [1.0]" in INBOX_SOURCE
