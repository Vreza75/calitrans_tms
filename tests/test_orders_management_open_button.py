"""Regression tests for Issue 3 (transitional Streamlit UX pass): Orders /
Load Management must open a record via an explicit "Open" action, not
dataframe checkbox/row selection - closing the resulting dialog must not
leave any selection state sticky (the bug this replaces: closing the
dialog left the row still checked, making the next interaction awkward).

Source-inspection assertions follow the same style already used in
tests/test_operations_inbox_ui_cleanup.py::
test_queue_is_button_driven_not_dataframe_selection_driven. Behavioral
assertions drive the real click flow through streamlit.testing.v1.AppTest,
same technique as tests/test_orders_management_dialog.py.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
ORDERS_SOURCE = (ROOT / "pages_app" / "orders_management.py").read_text(encoding="utf-8")

_APP_SCRIPT = """
import pandas as pd
from application.auth.models import AuthenticatedActor, Role
from pages_app.orders_management import render_orders_management

df = pd.DataFrame([{
    "_row_id": 1,
    "TYPE": "Import",
    "Booking Number": "BOOK-1",
    "Load ID": "",
    "Reference Number": "",
    "Customer": "Acme Corp",
    "Container Number": "CONT-1",
    "Port": "Port of Houston",
    "Warehouse": "Acme Warehouse",
    "Address": "",
    "Delivery Need Date": "2026-08-20",
    "LFD": "2026-08-21",
    "Status": "New",
    "Dispatcher Notes": "",
}])

principal = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
render_orders_management(df, principal)
"""


def _run() -> AppTest:
    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)
    assert not at.exception, at.exception
    return at


def test_order_list_is_button_driven_not_dataframe_selection_driven():
    assert 'selection_mode="single-row"' not in ORDERS_SOURCE
    assert "on_click=_open_order_group" in ORDERS_SOURCE
    assert '"Open"' in ORDERS_SOURCE


def test_open_button_is_visible_before_any_selection():
    at = _run()
    open_buttons = [b for b in at.button if b.label == "Open"]
    assert len(open_buttons) == 1
    # No editor content should render until Open is clicked.
    assert not any("Order Detail Editor" in m.value for m in at.markdown)


def test_clicking_open_shows_the_detail_dialog():
    at = _run()
    open_btn = next(b for b in at.button if b.label == "Open")
    open_btn.click().run(timeout=15)

    assert not at.exception
    assert "orders_management_selected_row_id" in at.session_state
    assert at.session_state["orders_management_selected_row_id"] == 1
    assert any(b.label == "Save Order Updates" for b in at.button)


def test_closing_the_dialog_clears_all_selection_state_no_sticky_reopen():
    at = _run()
    open_btn = next(b for b in at.button if b.label == "Open")
    open_btn.click().run(timeout=15)
    assert any(b.label == "Back to List" for b in at.button)

    back_btn = next(b for b in at.button if b.label == "Back to List")
    back_btn.click().run(timeout=15)
    # Back to List calls st.rerun() itself (not an on_click callback) -
    # matches Dispatch Board/Operations Inbox's identical pattern, which
    # needs one more settle pass under AppTest (a testing-harness quirk,
    # not app behavior - a real browser rerenders this automatically).
    at.run(timeout=15)

    assert not at.exception
    assert "orders_management_selected_row_id" not in at.session_state
    assert "orders_management_selected_context" not in at.session_state
    assert "orders_management_selected_group_ids" not in at.session_state
    # The dialog is gone - only the list's Open button remains, nothing
    # from the editor persists (no sticky "selected" state to reopen it).
    assert [b.label for b in at.button] == ["Open"]


def test_another_row_can_be_opened_immediately_after_closing():
    """A second, different order must open cleanly right after the first
    one closes - proves closing didn't leave stale context/group state
    that would misroute the next Open click."""
    script = """
import pandas as pd
from application.auth.models import AuthenticatedActor, Role
from pages_app.orders_management import render_orders_management

df = pd.DataFrame([
    {
        "_row_id": 1, "TYPE": "Import", "Booking Number": "BOOK-1", "Load ID": "",
        "Reference Number": "", "Customer": "Acme Corp", "Container Number": "CONT-1",
        "Port": "Port of Houston", "Warehouse": "Acme Warehouse", "Address": "",
        "Delivery Need Date": "2026-08-20", "LFD": "2026-08-21", "Status": "New",
        "Dispatcher Notes": "",
    },
    {
        "_row_id": 2, "TYPE": "Import", "Booking Number": "BOOK-2", "Load ID": "",
        "Reference Number": "", "Customer": "Beta LLC", "Container Number": "CONT-2",
        "Port": "Port of Houston", "Warehouse": "Beta Warehouse", "Address": "",
        "Delivery Need Date": "2026-08-22", "LFD": "2026-08-23", "Status": "New",
        "Dispatcher Notes": "",
    },
])

principal = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
render_orders_management(df, principal)
"""
    at = AppTest.from_string(script)
    at.run(timeout=15)
    assert not at.exception

    open_buttons = [b for b in at.button if b.label == "Open"]
    assert len(open_buttons) == 2

    # Rows sort by _row_id descending (newest first), so identify each
    # Open button by its key suffix (row id) rather than list position.
    def _open_button_for_row(row_id: int):
        return next(b for b in at.button if b.label == "Open" and b.key.endswith(f"_{row_id}"))

    _open_button_for_row(1).click().run(timeout=15)
    back_btn = next(b for b in at.button if b.label == "Back to List")
    back_btn.click().run(timeout=15)
    at.run(timeout=15)

    assert len([b for b in at.button if b.label == "Open"]) == 2
    _open_button_for_row(2).click().run(timeout=15)

    assert not at.exception
    assert at.session_state["orders_management_selected_row_id"] == 2


def test_changing_queue_preserves_nothing_stale_and_clears_editor():
    """Switching queues already calls clear_order_editor() - verify it now
    also clears orders_management_selected_group_ids (previously left
    behind, a latent stale-state bug independent of the checkbox fix)."""
    at = _run()
    open_btn = next(b for b in at.button if b.label == "Open")
    open_btn.click().run(timeout=15)
    assert "orders_management_selected_group_ids" in at.session_state

    queue_radio = next(r for r in at.radio if r.label == "Order Queue")
    queue_radio.set_value("Missing Info").run(timeout=15)

    assert not at.exception
    assert "orders_management_selected_row_id" not in at.session_state
    assert "orders_management_selected_context" not in at.session_state
    assert "orders_management_selected_group_ids" not in at.session_state
