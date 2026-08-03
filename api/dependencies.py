from __future__ import annotations

"""Phase 1 does not add production authentication (none exists yet to
build on - see docs/architecture/BACKEND_BOUNDARY_PHASE_1.md, "Known
limitations"). This module exists so routers depend on a named actor
resolver now, instead of reading a hardcoded string - swapping in real
auth later (API key, JWT, session) means changing this one function, not
every router.
"""

DEFAULT_ACTOR = "api"


def get_current_actor() -> str:
    """Placeholder actor resolver. Replace with real authentication before
    exposing this API outside a trusted network."""
    return DEFAULT_ACTOR
