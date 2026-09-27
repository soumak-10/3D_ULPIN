"""Create the package __init__ files the backend needs to import.

Run once: ``python scripts/make_packages.py`` from ``apps/api``. Kept in the
repository rather than done by hand so the layout is reproducible and a reviewer
can see exactly which packages exist and why.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"

# One entry per package. The docstring is the package's own explanation of what
# belongs in it — these are read by anyone navigating the tree for the first
# time, so they say what the layer is *for*, not what it contains.
PACKAGES: dict[str, str] = {
    ".": '''"""3D ULPIN Generation and Vertical Property Mapping System — API.

Layering, outermost first:

``api``          HTTP surface. Routing, status codes, OpenAPI. Checks capability.
``services``     Domain orchestration and transactions. Checks row-level scope.
``repositories`` Query construction. No policy, no commits.
``models``       SQLAlchemy mappers — the schema in Python.
``schemas``      Pydantic request/response contracts.
``core``         Cross-cutting: settings, security primitives, permissions, errors.
``ulpin``        The identifier itself: compose, parse, checksum. Pure functions.

The arrow points one way. ``services`` never imports from ``api``; ``ulpin``
imports from nothing but the standard library.
"""

__version__ = "1.0.0"
''',
    "api": '''"""HTTP layer.

Routers translate between the wire and the service layer and nothing more. A
handler that contains a business rule is a handler in the wrong place.
"""
''',
    "api/v1": '''"""Version 1 of the public API.

The version lives in the URL, not in a header, so a cached response and a
bookmarked link both stay unambiguous. Breaking changes open ``v2`` alongside
this package rather than mutating it.
"""
''',
    "api/v1/endpoints": '''"""Route modules, one per resource.

Each exposes a module-level ``router``; ``app.api.v1.router`` is the only place
that mounts them.
"""
''',
    "core": '''"""Cross-cutting concerns.

Settings, password and token primitives, the permission matrix, the exception
hierarchy and the RFC 9457 error handlers. Everything here is importable from
every other layer, so nothing here may import from any of them.
"""
''',
    "db": '''"""Database engine, session factory and the declarative base."""
''',
    "middleware": '''"""ASGI middleware.

Correlation IDs, security headers and rate limiting — the work that must happen
whether or not a route matched. Authentication is deliberately *not* here; it is
a dependency, so OpenAPI can state which routes require it.
"""
''',
    "repositories": '''"""Data access.

A repository builds queries and returns mapped objects. It does not commit, does
not raise HTTP errors and does not decide who is allowed to see what — those are
the service layer's job.
"""
''',
    "schemas": '''"""Pydantic contracts.

The request models are the first line of validation: a payload that cannot
express an invalid state never reaches the service. Response models exist partly
to *withhold* — password hashes and national identity numbers have no field to
be serialised into.
"""
''',
    "services": '''"""Domain services.

This is where a unit of work lives: load, check scope, mutate, commit. Services
own the transaction boundary and the row-level authorisation decisions that the
routing layer cannot make because it has not loaded the row.
"""
''',
    "ulpin": '''"""The 3D ULPIN itself.

Composition, parsing, the storey and unit codes, and the ISO 7064 MOD 36,36
check character. Pure functions over strings with no I/O and no framework
imports, because the identical algorithm also runs in PL/pgSQL and TypeScript
and the three must agree byte for byte.
"""
''',
}


def main() -> int:
    written = 0
    for relative, body in PACKAGES.items():
        directory = ROOT if relative == "." else ROOT / relative
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "__init__.py"
        if target.exists():
            print(f"skip   {target.relative_to(ROOT.parent)}")
            continue
        target.write_text(body, encoding="utf-8")
        print(f"create {target.relative_to(ROOT.parent)}")
        written += 1
    print(f"\n{written} created, {len(PACKAGES) - written} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
