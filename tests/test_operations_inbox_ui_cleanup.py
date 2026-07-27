from __future__ import annotations

from pathlib import Path

from ui_components.operations_inbox_state import (
    close_work_item,
    initialize_work_item_state,
    open_work_item,
    should_show_contextual_load_match,
)


ROOT = Path(__file__).resolve().parents[1]
INBOX_SOURCE = (ROOT / "pages_app" / "operations_inbox.py").read_text(encoding="utf-8")
ADMIN_SOURCE = (ROOT / "pages_app" / "email_imports.py").read_text(encoding="utf-8")


def test_open_work_item_requires_an_explicit_open_action() -> None:
    state: dict[str, object] = {}

    initialize_work_item_state(state)

    assert state["selected_work_item_id"] is None
    assert state["work_item_dialog_open"] is False

    open_work_item(state, work_item_id=42, queue_name="New Orders")

    assert state["selected_work_item_id"] == 42
    assert state["selected_work_item_queue"] == "New Orders"
    assert state["work_item_dialog_open"] is True


def test_closed_work_item_does_not_reopen_on_an_unrelated_rerun() -> None:
    state: dict[str, object] = {}
    open_work_item(state, work_item_id=42, queue_name="New Orders")

    close_work_item(state)
    initialize_work_item_state(state)

    assert state["selected_work_item_id"] is None
    assert state["work_item_dialog_open"] is False


def test_close_clears_temporary_attachment_state_for_only_that_work_item() -> None:
    state: dict[str, object] = {
        "selected_work_item_id": 42,
        "work_item_dialog_open": True,
        "operations_attachment_selection_42": 1,
        "operations_attachment_selection_99": 2,
    }

    close_work_item(state)

    assert "operations_attachment_selection_42" not in state
    assert state["operations_attachment_selection_99"] == 2


def test_queue_is_button_driven_not_dataframe_selection_driven() -> None:
    assert "open_work_item_{work_item_id}" in INBOX_SOURCE
    assert 'selection_mode="single-row"' not in INBOX_SOURCE[
        INBOX_SOURCE.index('st.caption(f"{len(tab_df)} work item(s)")') :
        INBOX_SOURCE.index("def _render_selected_operations_work_item")
    ]


def test_refresh_inbox_explicitly_clears_dialog_state() -> None:
    refresh_block = INBOX_SOURCE[
        INBOX_SOURCE.index('if st.button("Refresh Inbox"') :
        INBOX_SOURCE.index('if st.button("Recheck Next Batch"')
    ]

    assert "close_work_item(st.session_state)" in refresh_block


def test_dispatcher_workspace_hides_admin_and_routing_panels() -> None:
    for label in (
        "Latest Email Sync Result",
        "Email Sync Settings",
        "Email Sync KPIs",
        "Dispatcher Decision",
        "Email Synchronization Metadata",
        "Thread Sync Debug",
        "Bulk Cleanup / Learning Tools",
        "Control Center Snapshot",
        "Extracted Fields",
        "Advanced Triage / AI Details",
        "### Routing Actions",
    ):
        assert label not in INBOX_SOURCE


def test_admin_diagnostics_owns_email_sync_panels() -> None:
    for label in (
        "Email Synchronization",
        "Test IMAP Connections",
        "Latest Email Sync Result",
        "Email Sync Settings",
        "Email Sync KPIs",
    ):
        assert label in ADMIN_SOURCE


def test_order_review_combines_source_attachment_and_draft() -> None:
    assert "### Order Draft Review" in INBOX_SOURCE
    assert "#### Original Source Review" in INBOX_SOURCE
    assert "Attachments / Document Review" in INBOX_SOURCE
    assert "#### Extracted Order Draft" in INBOX_SOURCE
    assert "Create Order From Reviewed Draft" in INBOX_SOURCE


def test_existing_load_matching_is_contextual() -> None:
    assert "_should_show_contextual_load_match" in INBOX_SOURCE
    candidates = [{"Load ID": 123}]

    assert should_show_contextual_load_match(
        final_decision={"queue": "Existing Load Updates"},
        matched_load_id=None,
        load_match_candidates=candidates,
    )
    assert not should_show_contextual_load_match(
        final_decision={"queue": "New Orders"},
        matched_load_id=None,
        load_match_candidates=candidates,
    )
    assert not should_show_contextual_load_match(
        final_decision={"queue": "Existing Load Updates"},
        matched_load_id=123,
        load_match_candidates=candidates,
    )
