# application/auth/models.py

from __future__ import annotations

"""Canonical role/principal model shared by the FastAPI bearer-token layer
(api/auth.py) and the Streamlit session layer (ui_components/auth_gate.py).
Framework-neutral: no streamlit, no fastapi import here - both callers
depend on this module, not on each other.
"""

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    DISPATCHER = "dispatcher"
    MANAGER = "manager"
    ACCOUNTING = "accounting"
    ADMIN = "admin"


@dataclass(frozen=True)
class AuthenticatedActor:
    """A resolved identity: who they are (actor - email or dev-mode label)
    and what they're allowed to do (role). Same shape regardless of
    whether resolution happened via API bearer token or Streamlit
    session login."""

    actor: str
    role: Role
