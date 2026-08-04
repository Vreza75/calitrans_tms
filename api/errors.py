from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from application.exceptions import CommandFailedError, ConflictError, NotFoundError, ValidationError

logger = logging.getLogger("api.errors")


def _envelope(status_code: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    """Consistent error shape for every error response this API returns -
    application errors, FastAPI's own validation/HTTPException errors, and
    the catch-all 500 all go through this so a client never has to branch
    on which layer raised the error."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return _envelope(404, "NOT_FOUND", str(exc))

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError) -> JSONResponse:
        return _envelope(422, "VALIDATION_ERROR", str(exc))

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return _envelope(409, "CONFLICT", str(exc))

    @app.exception_handler(CommandFailedError)
    async def _command_failed(request: Request, exc: CommandFailedError) -> JSONResponse:
        return _envelope(400, "COMMAND_FAILED", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Malformed/missing request fields (Pydantic body validation) -
        # FastAPI's default shape is {"detail": [...]}; normalized to the
        # same envelope as every other error here.
        return _envelope(422, "VALIDATION_ERROR", "Request validation failed.", {"errors": exc.errors()})

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        # Covers 401/403 from api/auth.py's require_auth()/require_role()
        # (raised as plain HTTPException) and any other explicit
        # HTTPException in the codebase - same envelope, not FastAPI's
        # default {"detail": ...}.
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN"}.get(exc.status_code, "HTTP_ERROR")
        return _envelope(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never echo the raw exception (database errors can contain
        # connection strings, table/column names, or query fragments) back
        # to a client - log it server-side and return a generic message.
        logger.exception("Unhandled API error on %s %s", request.method, request.url.path)
        return _envelope(500, "INTERNAL_ERROR", "An internal error occurred.")
