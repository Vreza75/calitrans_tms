from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class _RequestBodyTooLarge(Exception):
    pass


def _too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": {
                "code": "PAYLOAD_TOO_LARGE",
                "message": "Request body exceeds the maximum allowed upload size.",
                "details": {},
            }
        },
    )


class RequestBodySizeLimitMiddleware:
    """Pure-ASGI middleware enforcing a hard ceiling on request body size
    for a specific set of (method, path-pattern) routes, counted from
    actual streamed bytes as they arrive - never from the client-supplied
    Content-Length header, which can be absent (chunked transfer) or
    simply wrong.

    This runs ahead of FastAPI's multipart parser, which only starts
    reading the body once a route's `UploadFile = File(...)` parameter is
    resolved during dependency injection. Wrapping `receive()` here means
    an oversized request is caught as soon as the running total crosses
    the limit - before any of it is spooled to memory or disk by the
    multipart parser - and the body is never buffered in full to measure
    it; only the running byte count is kept.

    FastAPI's own multipart parsing wraps body-read errors in a broad
    `except Exception` and turns them into its own HTTPException(400) -
    so raising out of `receive()` alone would get silently remapped to
    the wrong status by the time it reaches the router's exception
    middleware. Instead, the 413 is sent directly through the raw `send`
    the instant the limit is crossed, and `guarded_send` swallows every
    message the inner app attempts afterward (including that remapped
    400) so exactly one response - ours - reaches the client.

    Scoped to `guarded_routes` only: this is a transport-level backstop
    for the handful of routes built to accept large binary uploads, not
    a blanket body limit for the whole API (most routes are small JSON
    payloads with no need for this check)."""

    def __init__(self, app: ASGIApp, max_bytes: int, guarded_routes: set[tuple[str, re.Pattern[str]]]) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.guarded_routes = guarded_routes

    def _is_guarded(self, scope: Scope) -> bool:
        method = scope.get("method")
        path = scope.get("path", "")
        return any(
            method == guarded_method and pattern.fullmatch(path) for guarded_method, pattern in self.guarded_routes
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_guarded(scope):
            await self.app(scope, receive, send)
            return

        total = 0
        limit_exceeded = False

        async def guarded_send(message: object) -> None:
            if limit_exceeded:
                return
            await send(message)

        async def limited_receive():
            nonlocal total, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    limit_exceeded = True
                    await _too_large_response()(scope, receive, send)
                    raise _RequestBodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            pass
