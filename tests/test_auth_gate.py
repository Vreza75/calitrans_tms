from __future__ import annotations

from streamlit.testing.v1 import AppTest

import ui_components.auth_gate as auth_gate
from application.auth.models import AuthenticatedActor, Role

_APP_SCRIPT = """
import streamlit as st
from ui_components.auth_gate import require_login

principal = require_login()
st.text(f"WELCOME:{principal.actor}:{principal.role.value}")
"""


def test_unauthenticated_run_shows_login_form_not_page_content(monkeypatch) -> None:
    monkeypatch.delenv("STREAMLIT_AUTH_DEV_MODE", raising=False)

    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    assert len(at.text_input) == 2  # email + password
    assert not any("WELCOME:" in element.value for element in at.text)


def test_dev_mode_bypasses_login_form_entirely(monkeypatch) -> None:
    monkeypatch.setenv("STREAMLIT_AUTH_DEV_MODE", "true")

    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    assert len(at.text_input) == 0
    assert any("WELCOME:dev-mode:admin" in element.value for element in at.text)


def test_dev_mode_value_must_be_exactly_true(monkeypatch) -> None:
    for value in ("1", "yes", "TRUE ", "enabled", ""):
        monkeypatch.setenv("STREAMLIT_AUTH_DEV_MODE", value)
        at = AppTest.from_string(_APP_SCRIPT)
        at.run(timeout=15)
        assert not at.exception
        if value.strip().lower() == "true":
            assert len(at.text_input) == 0, f"value={value!r} should enable dev mode"
        else:
            assert len(at.text_input) == 2, f"value={value!r} should NOT enable dev mode"


def test_get_current_principal_returns_none_before_login(monkeypatch) -> None:
    fake_session_state: dict = {}
    monkeypatch.setattr(auth_gate.st, "session_state", fake_session_state)

    assert auth_gate.get_current_principal() is None


def test_get_current_principal_returns_stored_actor_after_login(monkeypatch) -> None:
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    fake_session_state = {auth_gate._SESSION_KEY: actor}
    monkeypatch.setattr(auth_gate.st, "session_state", fake_session_state)

    assert auth_gate.get_current_principal() is actor
