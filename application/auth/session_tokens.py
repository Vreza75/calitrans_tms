# application/auth/session_tokens.py

from __future__ import annotations

"""Stateless, signed session tokens for browser login (Phase 10 web
client). Framework-neutral - no fastapi import here, so this is testable
and reusable independent of api/auth.py's route-level wiring.

Why not a JWT library or a server-side session store: this repo has no
Supabase Auth project and no session-store infrastructure (see
api/auth.py's module docstring). An HMAC-signed, time-limited token
carrying {actor, role, exp} gives real tamper-evidence and expiry with
zero new infrastructure - stdlib hmac/hashlib only. Swapping this for
real Supabase JWT verification later means changing this module and
api/auth.py's _resolve_actor_from_token(); callers of issue/verify do not
need to change.

Configuration:

    SESSION_SECRET_KEY: required to issue or verify tokens. Never commit
        a real value - set via environment/secrets manager. If unset,
        issue_session_token() raises RuntimeError (fail closed - no
        silent default secret) and verify_session_token() always returns
        None (an unverifiable token is never treated as valid).
"""

import base64
import hashlib
import hmac
import json
import os
import time

from application.auth.models import AuthenticatedActor, Role

_DEFAULT_TTL_SECONDS = 12 * 60 * 60  # 12 hours - short-lived, matches "smallest secure bridge".


def _secret() -> bytes | None:
    raw = os.environ.get("SESSION_SECRET_KEY", "").strip()
    return raw.encode("utf-8") if raw else None


def _sign(payload_b64: bytes, secret: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret, payload_b64, hashlib.sha256).digest()).decode("ascii").rstrip("=")


def issue_session_token(actor: AuthenticatedActor, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    """Returns a new signed token for `actor`, valid for `ttl_seconds`.
    Raises RuntimeError if SESSION_SECRET_KEY is not configured - this is
    a server misconfiguration, not a client error, and the API layer
    translates it into a generic 500 rather than ever issuing an
    unsigned/weakly-signed token."""
    secret = _secret()
    if secret is None:
        raise RuntimeError("SESSION_SECRET_KEY is not configured - cannot issue session tokens.")

    payload = {"actor": actor.actor, "role": actor.role.value, "exp": int(time.time()) + max(1, ttl_seconds)}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    signature = _sign(payload_b64, secret)
    return f"{payload_b64.decode('ascii')}.{signature}"


def verify_session_token(token: str) -> AuthenticatedActor | None:
    """Returns the resolved actor if `token` is a validly-signed,
    unexpired session token; None for any other reason (malformed,
    unsigned, tampered, expired, unrecognized role, or
    SESSION_SECRET_KEY unset). Never raises for a bad/foreign token -
    only a genuinely malformed configuration should crash the request,
    and that path (issue_session_token) is separate from this one."""
    secret = _secret()
    if secret is None or not token or "." not in token:
        return None

    payload_b64_str, _, signature = token.rpartition(".")
    if not payload_b64_str or not signature:
        return None

    payload_b64 = payload_b64_str.encode("ascii")
    expected_signature = _sign(payload_b64, secret)
    if not hmac.compare_digest(expected_signature, signature):
        return None

    try:
        padded = payload_b64 + b"=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None

    actor = str(payload.get("actor") or "").strip()
    role_value = str(payload.get("role") or "").strip().lower()
    if not actor or role_value not in {r.value for r in Role}:
        return None

    return AuthenticatedActor(actor=actor, role=Role(role_value))
