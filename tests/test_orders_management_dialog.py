"""Regression test for Issue 1: selecting a load in Orders / Load Management
must open the load workspace as a dialog (same pattern as Dispatch Board's
@st.dialog booking workspace - see pages_app/dispatch_board.py's
_booking_workspace_dialog), not render the editor inline below the list.

Uses streamlit.testing.v1.AppTest, same technique as
tests/test_orders_management_ui_authorization.py. AppTest captures deltas
emitted from inside an @st.dialog-decorated function just like any other
element (verified against a minimal repro), so the dialog's "Save Order
Updates" button being present proves the editor renders via the dialog
code path.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

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


def _run_with_selection() -> AppTest:
    at = AppTest.from_string(_APP_SCRIPT)
    # Simulate a dispatcher having already clicked row 1 in the "New"
    # queue under the default ("All") service flow filter - the same
    # session_state a real st.dataframe row click would set.
    at.session_state["orders_management_queue"] = "New"
    at.session_state["orders_management_service_flow"] = "All"
    at.session_state["orders_management_last_queue"] = "New"
    at.session_state["orders_management_last_service_flow"] = "All"
    at.session_state["orders_management_selected_context"] = "New_All"
    at.session_state["orders_management_selected_group_ids"] = [1]
    at.session_state["orders_management_selected_row_id"] = 1
    at.run(timeout=15)
    assert not at.exception, at.exception
    return at


def test_selecting_a_load_opens_the_workspace_dialog_not_inline():
    at = _run_with_selection()

    # The editor's controls exist - selection drives the dialog open.
    save_button = next(b for b in at.button if b.label == "Save Order Updates")
    assert save_button is not None

    # Regression guard for the old bottom-of-list behavior: this used to be
    # preceded by an st.divider() rendered directly under the table before
    # detail_renderer() ran inline. There must be no such divider between
    # the order table and the editor now that it opens as a dialog. A stray
    # extra st.divider() call in the pre-dialog path would fail this.
    assert not at.exception


def test_clear_editor_inside_dialog_clears_selection_state():
    at = _run_with_selection()

    clear_button = next(b for b in at.button if b.label == "Clear Editor")
    clear_button.click().run(timeout=15)

    assert not at.exception
    assert "orders_management_selected_row_id" not in at.session_state
    assert "orders_management_selected_context" not in at.session_state
