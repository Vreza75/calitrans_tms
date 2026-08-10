from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors application services raise deliberately.

    Callers (Streamlit pages, FastAPI routers) catch this instead of the
    application layer rendering UI errors or shaping HTTP responses itself.
    """


class NotFoundError(ApplicationError):
    """The requested work item, load, or draft does not exist."""


class ValidationError(ApplicationError):
    """Caller-supplied input failed validation before any write happened."""


class CommandFailedError(ApplicationError):
    """A business command could not complete; no partial write was made."""


class ConflictError(ApplicationError):
    """The command is well-formed but conflicts with current state (an
    invalid status transition, a duplicate, a stale expected-state check).
    Maps to HTTP 409 - distinct from ValidationError (422, bad input) and
    CommandFailedError (400, an unexpected failure)."""
