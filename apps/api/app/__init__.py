"""3D ULPIN Generation and Vertical Property Mapping System — API.

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
