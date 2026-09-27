"""Wiring smoke tests.

Deliberately database-free. Everything here is about whether the application
assembles correctly — routes mounted, guards attached, schemas generatable,
errors shaped right. The moment a test needs a row in a table it belongs in
``test_property.py`` against a real PostGIS container, not here.

The OpenAPI test earns its place: a broken ``response_model`` or an unresolved
annotation passes ``import app.main`` and only fails when the schema is built.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.permissions import Permission, Role, has_permission, permissions_for
from app.main import app
from app.ulpin import codec


@pytest.fixture(scope="module")
def client() -> TestClient:
    # raise_server_exceptions=False so handled exceptions come back as responses
    # instead of propagating, which is what a real client would see.
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# Application assembly
# ===========================================================================
def test_liveness_needs_no_database(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_generates() -> None:
    spec = app.openapi()
    assert spec["info"]["title"].startswith("3D ULPIN")
    assert len(spec["paths"]) >= 30


def test_every_operation_has_a_summary() -> None:
    """A route without a summary renders as a bare path in the generated client
    and in the published docs. Catch it here rather than in review."""
    missing = [
        f"{method.upper()} {path}"
        for path, item in app.openapi()["paths"].items()
        for method, op in item.items()
        if method in {"get", "post", "patch", "put", "delete"} and not op.get("summary")
    ]
    assert not missing, f"operations without a summary: {missing}"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/buildings"),
        ("post", "/api/v1/buildings"),
        ("get", "/api/v1/units"),
        ("get", "/api/v1/owners"),
        ("get", "/api/v1/tenancies/me"),
        ("patch", "/api/v1/tenancies/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_protected_routes_reject_an_anonymous_caller(
    client: TestClient, method: str, path: str
) -> None:
    kwargs = {"json": {}} if method in {"post", "patch", "put"} else {}
    response = getattr(client, method)(path, **kwargs)

    # 401 before 422: an unauthenticated caller must not be able to probe a
    # schema by reading validation errors off a route they cannot reach.
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 401
    assert body["type"].startswith("https://")  # stable, documented error URI


def test_unknown_route_is_a_problem_document(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_correlation_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "smoke-test-1"})
    assert response.headers.get("X-Request-ID") == "smoke-test-1"


def test_baseline_security_headers_are_unconditional(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_csp_and_hsts_appear_outside_local(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSP and HSTS are gated on the environment — HSTS on a local HTTP origin
    would pin the browser to https://localhost and break development. The gate
    is easy to get backwards, so assert both sides of it."""
    from app.middleware import auth as middleware

    assert "Content-Security-Policy" not in client.get("/health").headers

    monkeypatch.setattr(middleware.settings, "ENVIRONMENT", "production")
    headers = client.get("/health").headers
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=")


# ===========================================================================
# RBAC matrix
# ===========================================================================
def test_only_officers_and_admins_may_register_buildings() -> None:
    assert has_permission(Role.ADMIN.value, Permission.BUILDING_CREATE)
    assert has_permission(Role.PROPERTY_OFFICER.value, Permission.BUILDING_CREATE)
    assert not has_permission(Role.OWNER.value, Permission.BUILDING_CREATE)
    assert not has_permission(Role.TENANT.value, Permission.BUILDING_CREATE)


def test_auditor_is_read_only() -> None:
    writes = {p for p in permissions_for(Role.AUDITOR.value) if ":create" in p or ":delete" in p}
    assert writes == set(), f"auditor holds write permissions: {sorted(writes)}"


def test_tenant_cannot_manage_ownership() -> None:
    assert not has_permission(Role.TENANT.value, Permission.OWNERSHIP_MANAGE)
    assert not has_permission(Role.OWNER.value, Permission.OWNERSHIP_MANAGE)


# ===========================================================================
# ULPIN codec — pure, no I/O
# ===========================================================================
SAMPLE = {
    "parcel_ulpin": "IN29BLR0001234",
    "block_code": "A1",
    "floor_number": 7,
    "unit_ordinal": 412,
}


def test_compose_parse_round_trip() -> None:
    composed = codec.compose(**SAMPLE)
    parts = codec.parse(composed)
    assert parts.parcel == "IN29BLR0001234"
    assert parts.block == "A1"
    assert parts.storey == "F007"
    assert parts.unit == "U0412"
    assert parts.code == codec.normalise(composed)
    assert parts.floor_number == 7
    assert parts.unit_ordinal == 412
    assert codec.verify(composed)


def test_the_parent_parcel_code_survives_unmodified() -> None:
    """Backward compatibility is the whole point: an existing 2D system must be
    able to slice the first 14 characters and still resolve the parcel."""
    composed = codec.compose(**SAMPLE)
    assert codec.normalise(composed)[:14] == "IN29BLR0001234"


def test_a_single_character_error_is_caught() -> None:
    good = codec.compose(**SAMPLE)
    corrupted = good.replace("F007", "F008", 1)
    assert not codec.verify(corrupted)


def test_the_unhyphenated_form_round_trips() -> None:
    composed = codec.compose(**SAMPLE)
    flat = composed.replace("-", "")
    assert len(flat) == 26
    assert codec.verify(flat)
    assert codec.parse(flat) == codec.parse(composed)


def test_basement_and_ground_are_distinguishable() -> None:
    assert codec.storey_code(-1) == "B001"
    assert codec.storey_code(0, "GROUND") == "G000"
    assert codec.storey_code(7) == "F007"
    assert codec.storey_code_to_floor("B002") == -2
    assert codec.storey_code_to_floor("G000") == 0


def test_a_basement_unit_cannot_collide_with_an_upper_one() -> None:
    """The storey segment separates B002 from F002, but the unit segment is
    shared — hence the reserved U9LNN band for basements."""
    assert codec.unit_code(12, floor_number=2) != codec.unit_code(12, floor_number=-2)
