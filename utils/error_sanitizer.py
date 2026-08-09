from __future__ import annotations

import re

_REDACTED = "***"

# user:password@ inside any DSN/URL (postgresql://, mysql://, redis://, ...)
# - the whole credential segment is replaced, not just the password: a
# username can itself be sensitive (an internal service account name).
_DSN_CREDENTIAL_PATTERN = re.compile(r"://[^:/@\s]+:[^@\s]+@")

# password=, token=, api_key=, secret=, access_token=, ... query params or
# key=value fragments embedded in an error message.
_CREDENTIAL_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)\b(password|pwd|passwd|token|api[_-]?key|secret|access[_-]?token)\s*=\s*[^&\s'\"]+"
)

_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.=]+")


def sanitize_message(message: str) -> str:
    """Redact credential material from an arbitrary error message.

    The single canonical sanitizer for this repository - lives in utils/
    (not application/ or db_client.py) because both the low-level db
    client and the application/api layers need it, and neither may depend
    on the other. Infrastructure exceptions (SQLAlchemy, psycopg2,
    requests, etc.) can embed a full connection DSN, a bearer token, or a
    credential query parameter directly in their str() representation.
    This is the single place that strips that material before it is
    allowed to reach an HTTP response, a wrapped application error, a
    schema-readiness detail string, or a log line.
    """
    if not message:
        return message

    sanitized = _DSN_CREDENTIAL_PATTERN.sub(f"://{_REDACTED}@", message)
    sanitized = _CREDENTIAL_QUERY_PARAM_PATTERN.sub(lambda m: f"{m.group(1)}={_REDACTED}", sanitized)
    sanitized = _BEARER_TOKEN_PATTERN.sub(f"Bearer {_REDACTED}", sanitized)
    return sanitized


def sanitize_exception_message(exc: BaseException) -> str:
    """Sanitized str(exc), safe to embed in a wrapped application error or
    an HTTP response body."""
    return sanitize_message(str(exc))
