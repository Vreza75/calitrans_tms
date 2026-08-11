"""Phase 5 Streamlit adapter test: proves the UI-level permission checks in
pages_app/orders_management.py actually disable the buttons they're
supposed to for a role lacking the permission - and that this is UX only,
not the security boundary (see tests/test_security_ui_bypass.py and
tests/test_load_commands_authorization.py for the boundary itself).

Renders _render_order_detail_editor directly (not the full
render_orders_management page, which needs a fully-populated loads
DataFrame with dataframe-selection interaction this test doesn't need to
exercise) via AppTest, same technique as
tests/test_router_permission_gate.py.
"""
from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

_APP_SCRIPT = """
import pandas as pd
from application.auth.models import AuthenticatedActor, Role
from pages_app.orders_management import _render_order_detail_editor

work_df = pd.DataFrame([{{
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
}}])

principal = AuthenticatedActor(actor="{actor_email}", role=Role.{role})
_render_order_detail_editor(work_df, 1, "test_context", principal)
"""


def _run(role: str, actor_email: str) -> AppTest:
    at = AppTest.from_string(_APP_SCRIPT.format(role=role, actor_email=actor_email))
    at.run(timeout=15)
    assert not at.exception, at.exception
    return at


def test_accounting_sees_disabled_save_and_quick_action_buttons():
    at = _run("ACCOUNTING", "accounting@calitranscorp.com")

    save_button = next(b for b in at.button if b.label == "Save Order Updates")
    assert save_button.disabled is True

    mark_missing = next(b for b in at.button if "Mark Missing Info" in b.label)
    mark_verified = next(b for b in at.button if "Mark Booking Verified" in b.label)
    cancel_order = next(b for b in at.button if "Cancel Order" in b.label)
    assert mark_missing.disabled is True
    assert mark_verified.disabled is True
    assert cancel_order.disabled is True

    captions = [c.value for c in at.caption]
    assert any("does not have permission to edit orders" in c for c in captions)
    assert any("disabled for your role" in c for c in captions)


def test_dispatcher_sees_enabled_save_and_quick_action_buttons():
    at = _run("DISPATCHER", "dispatcher@calitranscorp.com")

    save_button = next(b for b in at.button if b.label == "Save Order Updates")
    assert save_button.disabled is False

    mark_missing = next(b for b in at.button if "Mark Missing Info" in b.label)
    mark_verified = next(b for b in at.button if "Mark Booking Verified" in b.label)
    cancel_order = next(b for b in at.button if "Cancel Order" in b.label)
    assert mark_missing.disabled is False
    assert mark_verified.disabled is False
    assert cancel_order.disabled is False
