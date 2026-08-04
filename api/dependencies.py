from __future__ import annotations

"""Route-level auth dependencies. Real bearer-token authentication lives
in api/auth.py (require_auth / require_role / role groups) - this module
adapts that to the shape application command functions expect (a plain
actor-identity string for audit records)."""

from fastapi import Depends

from api.auth import AuthenticatedActor, require_auth


def get_current_actor(actor: AuthenticatedActor = Depends(require_auth)) -> str:
    """Authenticated actor identity as a plain string, for application
    commands that record it (e.g. close_work_item's audit event actor).
    Requires authentication - see api/auth.py::require_auth. Route-level
    role restrictions are separate (api.auth.require_role /
    READ_OPERATIONS / MUTATE_OPERATIONS / etc.) and must still be applied
    where a route needs more than "any authenticated actor".
    """
    return actor.actor
