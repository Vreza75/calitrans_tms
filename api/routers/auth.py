from __future__ import annotations

"""Phase 10 browser login bridge. Issues/verifies signed session tokens
(application/auth/session_tokens.py) backed by the existing app_users
table (repositories/user_repo.py) - the same identity store the
Streamlit login gate (ui_components/auth_gate.py) already uses via
application.auth.queries.authenticate_user. No second identity system:
both callers resolve to the same AuthenticatedActor shape."""

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import AuthenticatedActor, require_auth
from api.rate_limit import enforce_login_rate_limit
from api.schemas.auth import LoginIn, LoginOut, MeOut
from application.auth.permissions import ROLE_PERMISSIONS
from application.auth.queries import authenticate_user
from application.auth.session_tokens import issue_session_token
from repositories.user_repo import get_user_by_email

router = APIRouter(prefix="/auth", tags=["auth"])

# GET /me is spec'd (Phase 10 web client bootstrap) as a bare /api/v1/me
# route, not /api/v1/auth/me - a separate, unprefixed router registers it
# at the right path while still living in this module next to login().
me_router = APIRouter(tags=["auth"])


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, request: Request) -> LoginOut:
    enforce_login_rate_limit(request, payload.email)
    actor = authenticate_user(payload.email, payload.password)
    if actor is None:
        # Deliberately generic - do not reveal whether the email exists.
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = issue_session_token(actor)
    return LoginOut(token=token, actor=actor.actor, role=actor.role.value)


@me_router.get("/me", response_model=MeOut)
def me(actor: AuthenticatedActor = Depends(require_auth)) -> MeOut:
    """The frontend's auth/permission bootstrap (Step 9/10 of the Phase 10
    spec). display_name falls back to the actor identity string for
    principals with no app_users row (dev-mode, a static API_AUTH_TOKENS
    entry) - never a hard failure just to render a name."""
    user = get_user_by_email(actor.actor)
    display_name = str(user["display_name"]) if user and user.get("display_name") else actor.actor

    permissions = sorted(p.value for p in ROLE_PERMISSIONS.get(actor.role, frozenset()))
    return MeOut(email=actor.actor, display_name=display_name, role=actor.role.value, permissions=permissions)
