# application/auth/queries.py

from __future__ import annotations

"""Framework-neutral login verification - no streamlit import here so this
is reusable from a future FastAPI login endpoint as well as the Streamlit
session gate (ui_components/auth_gate.py)."""

from application.auth.models import AuthenticatedActor, Role
from application.auth.password import verify_password
from repositories.user_repo import get_user_by_email


def authenticate_user(email: str, password: str) -> AuthenticatedActor | None:
    """Returns the resolved actor on success, None on any failure (unknown
    email, wrong password, disabled account, unrecognized stored role).
    Never raises for bad credentials - only a real infrastructure error
    (e.g. SchemaNotReadyError) propagates."""
    email = (email or "").strip()
    if not email or not password:
        return None

    user = get_user_by_email(email)
    if user is None or not user.get("is_active", False):
        return None

    if not verify_password(password, str(user.get("password_hash") or "")):
        return None

    role_value = str(user.get("role") or "").strip().lower()
    try:
        role = Role(role_value)
    except ValueError:
        return None

    return AuthenticatedActor(actor=str(user["email"]), role=role)
