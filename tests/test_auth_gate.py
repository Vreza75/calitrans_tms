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


class _FakeContext:
    def __init__(self, cookies: dict) -> None:
        self.cookies = cookies


def test_restore_from_cookie_returns_none_when_no_cookie(monkeypatch) -> None:
    monkeypatch.setattr(auth_gate.st, "context", _FakeContext({}))
    assert auth_gate._restore_from_cookie() is None


def test_restore_from_cookie_returns_none_for_garbage_token(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setattr(auth_gate.st, "context", _FakeContext({auth_gate._COOKIE_NAME: "not-a-real-token"}))
    assert auth_gate._restore_from_cookie() is None


def test_restore_from_cookie_returns_actor_for_valid_signed_token(monkeypatch) -> None:
    """Regression test for Issue 3 (Streamlit refresh-logout): a valid,
    unexpired session cookie must restore the same actor/role that issued
    it - the mechanism a real browser refresh relies on."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    from application.auth.session_tokens import issue_session_token

    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)
    monkeypatch.setattr(auth_gate.st, "context", _FakeContext({auth_gate._COOKIE_NAME: token}))

    restored = auth_gate._restore_from_cookie()
    assert restored == actor


def test_restore_from_cookie_returns_none_without_configured_secret(monkeypatch) -> None:
    """No SESSION_SECRET_KEY configured must never treat any cookie value
    as valid - fail closed, same contract as session_tokens.py itself."""
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth_gate.st, "context", _FakeContext({auth_gate._COOKIE_NAME: "anything"}))
    assert auth_gate._restore_from_cookie() is None


def test_require_login_restores_session_from_cookie_without_showing_login_form(monkeypatch) -> None:
    """Regression test for Issue 3: require_login() must recover identity
    from the session cookie when st.session_state is empty (the refresh
    case), instead of falling through to the login form."""
    monkeypatch.delenv("STREAMLIT_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    from application.auth.session_tokens import issue_session_token

    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    token = issue_session_token(actor)

    fake_session_state: dict = {}
    monkeypatch.setattr(auth_gate.st, "session_state", fake_session_state)
    monkeypatch.setattr(auth_gate.st, "context", _FakeContext({auth_gate._COOKIE_NAME: token}))

    restored_principal = auth_gate.require_login()

    assert restored_principal == actor
    assert fake_session_state[auth_gate._SESSION_KEY] == actor


def test_require_login_falls_back_to_login_form_when_cookie_invalid(monkeypatch) -> None:
    """Regression test for Issue 3: an invalid/expired/tampered cookie
    must never restore a session - require_login() falls back to the
    real login form, driven through AppTest (not a direct bare-mode
    call) since _render_login_form() uses st.form/st.text_input, which
    need a real ScriptRunContext to avoid leaking Streamlit's internal
    form-tracking state into later tests in this process."""
    monkeypatch.delenv("STREAMLIT_AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setattr(auth_gate, "_restore_from_cookie", lambda: None)

    at = AppTest.from_string(_APP_SCRIPT)
    at.run(timeout=15)

    assert not at.exception
    assert len(at.text_input) == 2  # login form shown, no restored session
    assert not any("WELCOME:" in element.value for element in at.text)


_LOGOUT_SCRIPT = """
import streamlit as st
from ui_components.auth_gate import render_logout_control, require_login

principal = require_login()
render_logout_control()
st.text(f"WELCOME:{principal.actor}")
"""


def test_sign_out_clears_session_and_cookie_flag(monkeypatch) -> None:
    """Regression test for Issue 3: signing out must clear both the
    in-memory session and the cookie-set flag (so a stale cookie can't
    silently log the user back in on the next refresh)."""
    monkeypatch.delenv("STREAMLIT_AUTH_DEV_MODE", raising=False)

    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    at = AppTest.from_string(_LOGOUT_SCRIPT)
    # Seed session_state once, from the test (not re-seeded by the script
    # itself on every rerun) - the script under test only owns the sign-out
    # behavior, not identity provisioning.
    at.session_state[auth_gate._SESSION_KEY] = actor
    at.session_state[auth_gate._COOKIE_SET_FLAG] = True
    at.run(timeout=15)
    assert not at.exception
    assert any("WELCOME:dispatcher@calitranscorp.com" in element.value for element in at.text)

    sign_out_button = next(b for b in at.sidebar.button if b.label == "Sign Out")
    sign_out_button.click().run(timeout=15)

    assert not at.exception
    assert auth_gate._SESSION_KEY not in at.session_state
    assert auth_gate._COOKIE_SET_FLAG not in at.session_state
    # Signed out - back to the login form, not the page content.
    assert len(at.text_input) == 2
