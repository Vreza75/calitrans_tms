# ui_components/auth_gate.py
"""Streamlit session login gate. The only place in the app permitted to
touch st.session_state for identity - everything else (permission
decisions, credential verification) lives in application/auth/, which has
no streamlit import and is reusable from a future API login endpoint.

Session persistence across a browser refresh (STEP: refresh-logout fix):
st.session_state alone does not reliably survive a real browser refresh
(Streamlit is not guaranteed to rebind a new WebSocket connection to the
prior session). This reuses application/auth/session_tokens.py - the same
HMAC-signed, time-limited token already issued by the FastAPI web-client
bridge (api/auth.py) - stored in a browser cookie set via a small inline
script (Streamlit has no first-party cookie-write API; st.context.cookies
is read-only). The cookie is tamper-evident and expires like any other use
of that module - this does not weaken authentication, it only lets a
refresh recover a session verify_session_token() would already accept.
Documented pre-production follow-up, same as the Next.js web client: an
HttpOnly cookie set by the server would remove the need for client-side
JS entirely (see docs/architecture/WEB_CLIENT.md)."""

from __future__ import annotations

import json
import os

import streamlit as st
import streamlit.components.v1 as components

from application.auth.models import AuthenticatedActor, Role
from application.auth.queries import authenticate_user
from application.auth.session_tokens import issue_session_token, verify_session_token

_SESSION_KEY = "calitrans_authenticated_actor"
_COOKIE_NAME = "calitrans_session"
_COOKIE_SET_FLAG = "calitrans_session_cookie_set"
_COOKIE_MAX_AGE_SECONDS = 12 * 60 * 60  # matches session_tokens' default TTL

DEV_MODE_ACTOR = AuthenticatedActor(actor="dev-mode", role=Role.ADMIN)


def _set_session_cookie(token: str) -> None:
    cookie_value = f"{_COOKIE_NAME}={token}; path=/; max-age={_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax"
    components.html(f"<script>document.cookie = {json.dumps(cookie_value)};</script>", height=0)


def _clear_session_cookie() -> None:
    cookie_value = f"{_COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax"
    components.html(f"<script>document.cookie = {json.dumps(cookie_value)};</script>", height=0)


def _restore_from_cookie() -> AuthenticatedActor | None:
    token = st.context.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


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
            st.session_state.pop(_COOKIE_SET_FLAG, None)
            _clear_session_cookie()
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
    if principal is None:
        # A real browser refresh does not reliably preserve
        # st.session_state - try to restore identity from the signed
        # session cookie before falling back to the login form.
        restored = _restore_from_cookie()
        if restored is not None:
            st.session_state[_SESSION_KEY] = restored
            principal = restored

    if principal is None:
        _render_login_form()
        st.stop()

    if not st.session_state.get(_COOKIE_SET_FLAG):
        # Set once per session_state lifetime (fresh login or a
        # cookie-restored session both land here) - not on every rerun.
        try:
            token = issue_session_token(principal)
        except RuntimeError:
            # SESSION_SECRET_KEY not configured - login still works, it
            # just won't survive a browser refresh (same as before this
            # fix). Fail closed, never issue an unsigned cookie.
            token = None
        if token:
            _set_session_cookie(token)
        st.session_state[_COOKIE_SET_FLAG] = True

    return principal
