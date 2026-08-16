# ui_components/auth_gate.py
"""Streamlit session login gate. The only place in the app permitted to
touch st.session_state for identity - everything else (permission
decisions, credential verification) lives in application/auth/, which has
no streamlit import and is reusable from a future API login endpoint."""

from __future__ import annotations

import os

import streamlit as st

from application.auth.models import AuthenticatedActor, Role
from application.auth.queries import authenticate_user

_SESSION_KEY = "calitrans_authenticated_actor"

DEV_MODE_ACTOR = AuthenticatedActor(actor="dev-mode", role=Role.ADMIN)


def _dev_mode_enabled() -> bool:
    """Same contract as api/auth.py's API_AUTH_DEV_MODE: must be exactly
    'true' (case-insensitive). Unset, empty, or any other value always
    requires a real login - there is no implicit bypass."""
    return os.environ.get("STREAMLIT_AUTH_DEV_MODE", "").strip().lower() == "true"


def get_current_principal() -> AuthenticatedActor | None:
    principal = st.session_state.get(_SESSION_KEY)
    return principal if isinstance(principal, AuthenticatedActor) else None


def _render_login_form() -> None:
    st.title("CaliTrans TMS")
    st.subheader("Sign In")
    with st.form("calitrans_login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In")

    if submitted:
        actor = authenticate_user(email, password)
        if actor is None:
            st.error("Invalid email or password.")
        else:
            st.session_state[_SESSION_KEY] = actor
            st.rerun()


def render_logout_control() -> None:
    """Call from the sidebar once a principal is known. No-op (and no
    sign-out control shown) under dev mode - there is no session to end."""
    if _dev_mode_enabled():
        return
    principal = get_current_principal()
    if principal is None:
        return
    with st.sidebar:
        st.caption(f"Signed in as **{principal.actor}** ({principal.role.value})")
        if st.button("Sign Out", key="calitrans_sign_out"):
            st.session_state.pop(_SESSION_KEY, None)
            st.rerun()


def require_login() -> AuthenticatedActor:
    """Fail-closed gate: call at the very top of app.py, before any page
    content or navigation renders. Returns the authenticated actor on
    success. On failure, renders a login form and calls st.stop() - no
    page content, no sidebar navigation, nothing else on the page executes
    for this run."""
    if _dev_mode_enabled():
        st.session_state.setdefault(_SESSION_KEY, DEV_MODE_ACTOR)
        return DEV_MODE_ACTOR

    principal = get_current_principal()
    if principal is not None:
        return principal

    _render_login_form()
    st.stop()
