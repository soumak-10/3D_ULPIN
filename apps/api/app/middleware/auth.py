"""Request-scoped middleware: correlation IDs, security headers, rate limiting.

These run on every request, before routing. Authentication itself is a
*dependency*, not middleware — that way FastAPI can document which endpoints
require it and OpenAPI stays truthful. What lives here is the cross-cutting
work that must happen whether or not a route matched.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

NextCall = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attaches a correlation ID and logs one line per request.

    The ID is echoed in ``X-Request-ID`` and written into the audit trigger's
    ``app.request_id`` GUC, so a row in ``audit_logs`` can be traced back to the
    exact HTTP call that produced it.
    """

    async def dispatch(self, request: Request, call_next: NextCall) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed method=%s path=%s request_id=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                request_id,
                elapsed,
            )
            raise

        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed:.1f}"

        log = logger.warning if response.status_code >= 500 else logger.info
        log(
            "%s %s -> %s request_id=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            request_id,
            elapsed,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline browser hardening.

    The CSP allows ``blob:`` workers and ``data:``/``blob:`` images because the
    Three.js viewer decodes Draco geometry in a worker and uploads KTX2 textures
    from blobs. Dropping those breaks the 3D pane, not just a stylesheet.
    """

    async def dispatch(self, request: Request, call_next: NextCall) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(self), camera=(), microphone=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        if settings.ENVIRONMENT in ("staging", "production"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "worker-src 'self' blob:; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'",
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter keyed on client IP.

    In-process and therefore per-replica: three API pods mean three times the
    stated limit. That is acceptable as a first line of defence against
    credential stuffing, but the authoritative limit belongs at the edge. The
    nginx config in ``infra/nginx`` carries the cluster-wide rule.
    """

    def __init__(self, app, **kwargs) -> None:  # noqa: ANN001, ANN003
        super().__init__(app, **kwargs)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    _SENSITIVE = (
        "/auth/login",
        "/auth/register",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/refresh",
        "/auth/resend-verification",
    )

    async def dispatch(self, request: Request, call_next: NextCall) -> Response:
        if not settings.RATE_LIMIT_ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        sensitive = any(path.endswith(suffix) for suffix in self._SENSITIVE)
        limit = (
            settings.RATE_LIMIT_LOGIN_PER_MINUTE
            if sensitive
            else settings.RATE_LIMIT_DEFAULT_PER_MINUTE
        )

        forwarded = request.headers.get("x-forwarded-for")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        key = f"{ip}:{'auth' if sensitive else 'api'}"

        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= limit:
            retry_after = int(60 - (now - window[0])) + 1
            logger.warning("rate limit hit ip=%s path=%s", ip, path)
            return JSONResponse(
                status_code=429,
                content={
                    "type": "https://docs.ulpin.gov.in/errors/rate_limited",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": f"Rate limit exceeded. Retry in {retry_after}s.",
                    "code": "rate_limited",
                },
                headers={"Retry-After": str(retry_after)},
                media_type="application/problem+json",
            )

        window.append(now)

        # Bound memory: without this, every probing IP leaves a permanent entry.
        if len(self._hits) > 10_000:
            stale = [k for k, v in self._hits.items() if not v or now - v[-1] > 120]
            for k in stale:
                del self._hits[k]

        return await call_next(request)
