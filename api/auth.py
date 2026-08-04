from __future__ import annotations

"""API authentication and role authorization.

No Supabase Auth project exists in this repo yet (no JWT secret, no
auth.users, no login system anywhere) - only a raw Postgres connection
string. Real JWT verification against Supabase Auth is the preferred
long-term direction, but requires provisioning infrastructure this
codebase doesn't have and this change cannot create on its own. Until
that exists, this module implements a real, fail-closed bearer-token
scheme: tokens are configured out-of-band (API_AUTH_TOKENS env var, never
committed), each mapped to an actor identity and a role. Swapping this
for real Supabase JWT verification later means changing
_resolve_actor_from_token() - the require_auth()/require_role()
dependency shape routes depend on does not need to change.

Configuration:

    API_AUTH_TOKENS: JSON array of {"token": str, "actor": str, "role": str}.
        Never commit real values - set via environment/secrets manager.
        Example (illustrative, not a real credential):
            [{"token": "REPLACE_ME", "actor": "dispatcher@calitranscorp.com", "role": "dispatcher"}]

    API_AUTH_DEV_MODE: must be exactly "true" (case-insensitive) to bypass
        real token verification. Defaults to unset/false. When unset and
        API_AUTH_TOKENS is empty or missing, every protected route fails
        closed (401) - there is no universal bypass token.
"""

import json
import os
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Header, HTTPException


class Role(str, Enum):
    DISPATCHER = "dispatcher"
    MANAGER = "manager"
    ACCOUNTING = "accounting"
    ADMIN = "admin"


@dataclass(frozen=True)
class AuthenticatedActor:
    actor: str
    role: Role


DEV_MODE_ACTOR = AuthenticatedActor(actor="dev-mode", role=Role.ADMIN)


def _dev_mode_enabled() -> bool:
    return os.environ.get("API_AUTH_DEV_MODE", "").strip().lower() == "true"


def _load_configured_tokens() -> dict[str, AuthenticatedActor]:
    raw = os.environ.get("API_AUTH_TOKENS", "").strip()
    if not raw:
        return {}

    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        # Malformed config must not silently grant access - fail closed
        # (empty token map) rather than raising at import time and
        # crashing the whole API.
        return {}

    tokens: dict[str, AuthenticatedActor] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("token") or "").strip()
        actor = str(entry.get("actor") or "").strip()
        role_value = str(entry.get("role") or "").strip().lower()
        if not token or not actor or role_value not in {r.value for r in Role}:
            continue
        tokens[token] = AuthenticatedActor(actor=actor, role=Role(role_value))
    return tokens


def _resolve_actor_from_token(token: str) -> AuthenticatedActor | None:
    return _load_configured_tokens().get(token)


def require_auth(authorization: str | None = Header(default=None)) -> AuthenticatedActor:
    """FastAPI dependency: any authenticated actor, any role.

    Fails closed: no Authorization header, a malformed header, or a token
    not present in the configured set all return 401. There is no
    universal bypass token - dev mode is a distinct, explicit opt-in
    (API_AUTH_DEV_MODE=true), never a fallback when tokens are merely
    unconfigured.
    """
    if _dev_mode_enabled():
        return DEV_MODE_ACTOR

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    actor = _resolve_actor_from_token(token)
    if actor is None:
        raise HTTPException(status_code=401, detail="Invalid or unrecognized API token.")

    return actor


def require_role(*allowed_roles: Role):
    """FastAPI dependency factory: authenticate, then require the actor's
    role be one of `allowed_roles`. Raises 403 (not 401) for a valid actor
    with the wrong role - the two failure modes are deliberately distinct
    per the API contract (401 unauthenticated, 403 unauthorized)."""

    def _dependency(actor: AuthenticatedActor = Depends(require_auth)) -> AuthenticatedActor:
        if actor.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{actor.role.value}' is not permitted to perform this action.",
            )
        return actor

    return _dependency


# Named role groups matching the Phase 1 correction spec's permission table.
READ_OPERATIONS = (Role.DISPATCHER, Role.MANAGER, Role.ADMIN)
READ_LOADS = (Role.DISPATCHER, Role.MANAGER, Role.ADMIN, Role.ACCOUNTING)
MUTATE_OPERATIONS = (Role.DISPATCHER, Role.MANAGER, Role.ADMIN)
ADMIN_ONLY = (Role.ADMIN,)
