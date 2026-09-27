"""ASGI middleware.

Correlation IDs, security headers and rate limiting — the work that must happen
whether or not a route matched. Authentication is deliberately *not* here; it is
a dependency, so OpenAPI can state which routes require it.
"""
