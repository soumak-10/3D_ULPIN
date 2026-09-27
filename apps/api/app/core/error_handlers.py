"""Exception handlers rendering RFC 9457 ``application/problem+json``.

One shape for every error, so the frontend has a single parser. Unexpected
exceptions are logged with their traceback and returned without it — a stack
trace in an HTTP response is an information leak.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import ULPINException

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    request: Request,
    errors: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://docs.ulpin.gov.in/errors/{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
        "instance": str(request.url.path),
    }
    if errors:
        body["errors"] = errors
    if request_id := getattr(request.state, "request_id", None):
        body["request_id"] = request_id

    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=headers or {},
        media_type=PROBLEM_MEDIA_TYPE,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ULPINException)
    async def _domain(request: Request, exc: ULPINException) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("domain error code=%s", exc.code)
        else:
            logger.info("domain error code=%s detail=%s", exc.code, exc.detail)
        return _problem(
            status_code=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            request=request,
            errors=exc.errors or None,
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Collapse pydantic's positional error list into a field -> messages map,
        # which is what a form needs to highlight the offending input.
        field_errors: dict[str, list[str]] = {}
        for err in exc.errors():
            location = [str(part) for part in err["loc"] if part not in ("body", "query", "path")]
            field = ".".join(location) or "__root__"
            message = err.get("msg", "Invalid value")
            field_errors.setdefault(field, []).append(
                message.removeprefix("Value error, ")
            )

        return _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
            title="Validation Failed",
            detail="One or more fields failed validation.",
            request=request,
            errors=field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            400: "bad_request",
            401: "unauthenticated",
            403: "permission_denied",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            429: "rate_limited",
        }
        return _problem(
            status_code=exc.status_code,
            code=codes.get(exc.status_code, "http_error"),
            title=str(exc.detail) if exc.status_code < 500 else "Server Error",
            detail=str(exc.detail),
            request=request,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        """Database constraint violations that slipped past the service layer.

        Constraint names are mapped to human wording where a specific one is
        known; everything else gets a generic 409. The raw driver message is
        logged, never returned — it contains column values.
        """
        logger.warning("integrity error: %s", exc.orig)
        text = str(getattr(exc, "orig", exc))

        known = {
            "uq_users_email_lower": ("email_taken", "That email address is already registered."),
            "uq_ulpins_code": ("duplicate_ulpin", "That identifier already exists."),
            "uq_units_floor_number": (
                "duplicate_unit",
                "A unit with that number already exists on this floor.",
            ),
            "uq_floors_building_number": (
                "duplicate_floor",
                "That floor number is already recorded for this building.",
            ),
            "ex_tenants_no_overlap": (
                "tenancy_overlap",
                "This unit already has an active tenancy covering those dates.",
            ),
            "ck_ownership_share_sum": (
                "share_sum_invalid",
                "Ownership shares for a unit must total exactly 1.",
            ),
            "ck_ulpins_checksum": (
                "checksum_invalid",
                "The identifier failed its check-character validation.",
            ),
        }
        for name, (code, message) in known.items():
            if name in text:
                return _problem(
                    status_code=status.HTTP_409_CONFLICT,
                    code=code,
                    title="Conflict",
                    detail=message,
                    request=request,
                )

        return _problem(
            status_code=status.HTTP_409_CONFLICT,
            code="constraint_violation",
            title="Conflict",
            detail="The change conflicts with an existing record.",
            request=request,
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database error")
        return _problem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            title="Service Unavailable",
            detail="The registry database is temporarily unavailable.",
            request=request,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception")
        detail = (
            f"{type(exc).__name__}: {exc}"
            if settings.DEBUG
            else "An unexpected error occurred. Quote the request ID when reporting this."
        )
        return _problem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            title="Internal Server Error",
            detail=detail,
            request=request,
        )
