"""Mounts every v1 router.

The only place in the codebase that knows the full route table. Order matters
only where prefixes could shadow one another; these ten do not overlap.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    buildings,
    dashboard,
    fraud,
    owners,
    search,
    tenants,
    three_d_ulpin,
    ulpins,
    units,
    verification,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(buildings.router)
api_router.include_router(units.router)
api_router.include_router(owners.router)
api_router.include_router(tenants.router)
api_router.include_router(ulpins.router)
# /3d-ulpin, not a sub-path of /ulpins: the identifier module mints codes, this
# one places volumes. Nesting it would imply the geometry is a property of the
# code rather than the thing the code denotes.
api_router.include_router(three_d_ulpin.router)
api_router.include_router(verification.router)
api_router.include_router(fraud.router)
api_router.include_router(search.router)
api_router.include_router(dashboard.router)

__all__ = ["api_router"]
