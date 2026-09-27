"""The Python enums and the PostgreSQL enum types must not drift.

Every enum column in this application is mapped with ``create_type=False``:
SQLAlchemy sends the Python member's *value* as a string literal and PostgreSQL
resolves it against the type. So a Python member that the database type lacks is
not a type error, not a migration error, and not caught by ``mypy`` — it is a
``22P02 invalid input value for enum`` at 3am, on the one code path nobody
exercised.

The invariant is one-directional and deliberately so:

    every Python member must exist in the SQL type; the SQL type may hold more.

The database is allowed a richer vocabulary than the application currently uses
— that is how a value gets introduced ahead of the code that will write it. The
reverse is what breaks.

Parsing DDL with a regular expression is normally a poor idea. It is the right
one here: the alternative is a live PostgreSQL connection, which would make this
an integration test and take it out of the fast suite, and the subset of DDL
being matched is two fixed statement forms this repository writes itself.
"""

from __future__ import annotations

import enum
import re
from pathlib import Path

import pytest

from app.models import enums as py_enums

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "database" / "schemas"

# CREATE TYPE <name> AS ENUM ( ... );
_CREATE_ENUM = re.compile(
    r"CREATE\s+TYPE\s+(?:\w+\.)?(?P<name>\w+)\s+AS\s+ENUM\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
# ALTER TYPE <name> ADD VALUE [IF NOT EXISTS] '<value>';
_ALTER_ENUM = re.compile(
    r"ALTER\s+TYPE\s+(?:\w+\.)?(?P<name>\w+)\s+ADD\s+VALUE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?'(?P<value>[^']+)'",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"'([^']*)'")

# Python enum class -> PostgreSQL type name. Only classes that back a column
# belong here; VerificationOutcome and FraudRuleCode are included because the
# verification and fraud modules persist them.
ENUM_TO_PG_TYPE: dict[type[enum.Enum], str] = {
    py_enums.UserRole: "user_role",
    py_enums.UserStatus: "user_status",
    py_enums.BuildingStatus: "building_status",
    py_enums.BuildingUse: "building_use",
    py_enums.ConstructionStatus: "construction_status",
    py_enums.FloorType: "floor_type",
    py_enums.UnitType: "unit_type",
    py_enums.UnitStatus: "unit_status",
    py_enums.OccupancyStatus: "occupancy_status",
    py_enums.OwnerType: "owner_type",
    py_enums.OwnershipMode: "ownership_mode",
    py_enums.TenancyStatus: "tenancy_status",
    py_enums.UlpinType: "ulpin_type",
    py_enums.UlpinStatus: "ulpin_status",
    py_enums.VerificationType: "verification_type",
    py_enums.VerificationStatus: "verification_status",
    py_enums.VerificationOutcome: "verification_outcome",
    py_enums.FraudRuleCode: "fraud_alert_type",
    py_enums.AlertSeverity: "alert_severity",
    py_enums.AlertStatus: "alert_status",
    py_enums.AuditAction: "audit_action",
}


def _sql_text() -> str:
    """Every schema file, concatenated in load order.

    Order matters: a value added by ``05_modules.sql`` must count even though
    the type was created back in ``01_types.sql``.
    """
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    assert files, f"no schema files found under {SCHEMA_DIR}"
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def _pg_enum_members() -> dict[str, set[str]]:
    sql = _sql_text()
    members: dict[str, set[str]] = {}

    for match in _CREATE_ENUM.finditer(sql):
        name = match.group("name").lower()
        # Strip line comments before pulling quoted literals, so a value
        # mentioned only in a trailing -- comment is not counted as declared.
        body = re.sub(r"--[^\n]*", "", match.group("body"))
        members[name] = set(_QUOTED.findall(body))

    for match in _ALTER_ENUM.finditer(sql):
        members.setdefault(match.group("name").lower(), set()).add(match.group("value"))

    return members


@pytest.fixture(scope="module")
def pg_enums() -> dict[str, set[str]]:
    return _pg_enum_members()


def test_the_schema_files_are_parseable(pg_enums: dict[str, set[str]]) -> None:
    """Guard the guard: if the regex stops matching, every other test in this
    module would pass vacuously."""
    assert len(pg_enums) >= 15, f"only found {sorted(pg_enums)}"
    assert "user_role" in pg_enums
    assert pg_enums["user_role"] >= {"ADMIN", "PROPERTY_OFFICER", "OWNER", "TENANT"}


@pytest.mark.parametrize(
    "py_enum,pg_type",
    list(ENUM_TO_PG_TYPE.items()),
    ids=[f"{k.__name__}->{v}" for k, v in ENUM_TO_PG_TYPE.items()],
)
def test_every_python_member_exists_in_postgres(
    py_enum: type[enum.Enum], pg_type: str, pg_enums: dict[str, set[str]]
) -> None:
    assert pg_type in pg_enums, (
        f"{py_enum.__name__} maps to PostgreSQL type {pg_type!r}, which no schema "
        f"file declares. Known types: {sorted(pg_enums)}"
    )

    declared = pg_enums[pg_type]
    used = {m.value for m in py_enum}
    missing = used - declared

    assert not missing, (
        f"{py_enum.__name__} has {len(missing)} member(s) PostgreSQL type "
        f"{pg_type!r} does not accept: {sorted(missing)}.\n"
        f"Add them with:\n"
        + "\n".join(
            f"    ALTER TYPE ulpin.{pg_type} ADD VALUE IF NOT EXISTS '{v}';"
            for v in sorted(missing)
        )
    )
