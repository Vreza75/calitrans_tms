from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

# In-process sliding-window limiter - no new dependency (this project keeps
# a minimal footprint; see requirements.txt). Deliberately single-process:
# state lives in this module's dict, so it does not coordinate across
# multiple uvicorn workers or horizontally-scaled API instances. Acceptable
# for the current single-process deployment; a shared store (Redis, or the
# database itself) is the real fix before running more than one worker.
_WINDOW_SECONDS = 60
_MAX_ATTEMPTS = 5

_lock = threading.Lock()
_attempts: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request, email: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host}:{email.strip().lower()}"


def enforce_login_rate_limit(request: Request, email: str) -> None:
    """Raises HTTPException(429) once a client+email pair has made
    _MAX_ATTEMPTS login attempts (successful or not) within
    _WINDOW_SECONDS. Counting every attempt, not just failures, avoids
    leaking whether an email exists through differential rate-limit
    behavior."""
    key = _client_key(request, email)
    now = time.time()
    cutoff = now - _WINDOW_SECONDS

    with _lock:
        attempts = _attempts[key]
        while attempts and attempts[0] < cutoff:
            attempts.pop(0)

        if len(attempts) >= _MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Please wait a minute and try again.",
            )

        attempts.append(now)
