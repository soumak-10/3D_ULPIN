"""Application factory and process lifecycle.

Two health endpoints, deliberately different:

``/health``  liveness. Answers from the process alone, touches nothing external.
             A failure here means "restart me".
``/ready``   readiness. Opens a database connection and checks that PostGIS is
             present. A failure here means "take me out of the load balancer",
             which is not the same instruction at all.

Conflating the two is how a transient database blip turns into a rolling restart
of every replica.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.error_handlers import register_exception_handlers
from app.db.session import check_database_health, dispose_engine
from app.middleware.auth import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # These two are chatty at INFO and say nothing the request log does not.
    logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger.info(
        "starting %s v%s env=%s", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT
    )

    # Fail loudly at boot outside local: an API that starts without a database
    # only discovers the problem on the first user's request.
    try:
        health = await check_database_health()
        logger.info(
            "database ready postgres=%s postgis=%s sfcgal=%s",
            health["postgres"],
            health["postgis"],
            health["sfcgal"],
        )
    except Exception:
        if settings.ENVIRONMENT == "local":
            logger.warning("database unreachable at startup; continuing (local)", exc_info=True)
        else:
            logger.critical("database unreachable at startup", exc_info=True)
            raise

    yield

    await dispose_engine()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        lifespan=lifespan,
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json" if settings.DEBUG else None,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        description=(
            "Registry API for 3D ULPINs — the 14-character parcel identifier "
            "extended with block, storey and unit segments so that an "
            "individually owned volume inside a building has an identifier of "
            "its own. The parent parcel code is carried through unmodified, so "
            "every 3D identifier remains resolvable by existing 2D systems."
        ),
    )

    # Middleware runs bottom-up on the way in. TrustedHost must therefore be
    # added last so it runs first and rejects a forged Host header before any
    # other middleware has read it.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,  # the refresh cookie depends on this
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    if settings.ALLOWED_HOSTS != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["System"], summary="Liveness")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
        }

    @app.get("/ready", tags=["System"], summary="Readiness — database and PostGIS")
    async def ready() -> JSONResponse:
        try:
            payload = await check_database_health()
        except Exception as exc:
            logger.error("readiness probe failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": "database unreachable"},
            )
        if not payload.get("postgis"):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": "PostGIS extension missing"},
            )
        return JSONResponse(status_code=status.HTTP_200_OK, content=payload)

    return app


app = create_app()
