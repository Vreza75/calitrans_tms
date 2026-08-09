from __future__ import annotations

from streamlit.testing.v1 import AppTest

_APP_SCRIPT = """
from application.auth.models import AuthenticatedActor, Role
from ui_components.app_shell import render_sidebar

render_sidebar(principal=AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER),
               refresh_callback=lambda: None)
"""

_ADMIN_APP_SCRIPT = """
from application.auth.models import AuthenticatedActor, Role
from ui_components.app_shell import render_sidebar

render_sidebar(principal=AuthenticatedActor(actor="admin@calitranscorp.com", role=Role.ADMIN),
               refresh_callback=lambda: None)
"""


def test_dispatcher_sidebar_radio_never_offers_billing() -> None:
    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    radio = at.sidebar.radio[0]
    assert "Billing / ProfitTools" not in radio.options
    assert "Admin / Diagnostics" not in radio.options
    assert "Operations Inbox" in radio.options


def test_admin_sidebar_radio_offers_admin_diagnostics_umbrella() -> None:
    at = AppTest.from_string(_ADMIN_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    radio = at.sidebar.radio[0]
    assert "Admin / Diagnostics" in radio.options
