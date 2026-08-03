from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from application.exceptions import CommandFailedError, NotFoundError, ValidationError

logger = logging.getLogger("api.errors")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": str(exc)})

    @app.exception_handler(CommandFailedError)
    async def _command_failed(request: Request, exc: CommandFailedError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "command_failed", "detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never echo the raw exception (database errors can contain
        # connection strings, table/column names, or query fragments) back
        # to a client - log it server-side and return a generic message.
        logger.exception("Unhandled API error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "An internal error occurred."})
