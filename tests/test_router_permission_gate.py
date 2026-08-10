from __future__ import annotations

from streamlit.testing.v1 import AppTest

import pages_app.router as router_module
from application.auth.models import AuthenticatedActor, Role

_APP_SCRIPT = """
from application.auth.models import AuthenticatedActor, Role
from pages_app.router import route_selected_page

route_selected_page(AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER))
"""


def test_router_blocks_a_section_render_sidebar_should_never_return_for_this_role(monkeypatch) -> None:
    """render_sidebar already filters choices by role, but route_selected_page
    must not simply trust whatever section string comes back - this proves
    the independent fail-closed re-check actually runs, using a section
    dispatcher is not permitted to see (Billing / ProfitTools) as if the
    sidebar filter had somehow been bypassed."""
    monkeypatch.setattr(router_module, "render_sidebar", lambda **kwargs: "Billing / ProfitTools")

    rendered = {"called": False}
    monkeypatch.setattr(
        router_module, "_render_selected_page", lambda section, df, principal: rendered.__setitem__("called", True)
    )

    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    assert rendered["called"] is False
    assert any("do not have permission" in element.value.lower() for element in at.error)


def test_router_renders_a_permitted_section(monkeypatch) -> None:
    monkeypatch.setattr(router_module, "render_sidebar", lambda **kwargs: "Operations Inbox")

    rendered = {"section": None}

    def _fake_render(section, df, principal):
        rendered["section"] = section

    monkeypatch.setattr(router_module, "_render_selected_page", _fake_render)
    monkeypatch.setattr(router_module, "_load_current_tms_data_or_stop", lambda: __import__("pandas").DataFrame())

    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    assert rendered["section"] == "Operations Inbox"
