"""Emit ALTER TABLE ... ADD COLUMN for every mapped column the database lacks.

Companion to check_schema_drift.py. That script reports the gap; this one closes
it, using SQLAlchemy's own DDL compiler so the emitted type matches what the
model actually declares (including geoalchemy2 geometry columns) instead of a
hand-guessed equivalent.

Additive only. It never drops or renames anything, so seeded data and the
triggers, indexes and functions in database/schemas/ keep working untouched.
Columns are added nullable regardless of what the model says: an existing row
cannot satisfy NOT NULL retroactively, and the ORM supplies a value on insert
anyway.

    .venv/Scripts/python.exe scripts/sync_schema.py          # print the SQL
    .venv/Scripts/python.exe scripts/sync_schema.py --apply  # and run it
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.schema import CreateColumn

from app.db.base import Base
from app.db.session import engine

import app.models  # noqa: F401  -- registers the models on Base.metadata


async def main() -> int:
    apply = "--apply" in sys.argv

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT table_schema, table_name, column_name
                FROM information_schema.columns
                WHERE table_schema IN ('ulpin', 'public')
                """
            )
        )
        actual: dict[tuple[str, str], set[str]] = {}
        for schema, table, column in rows:
            actual.setdefault((schema, table), set()).add(column)

        dialect = conn.dialect
        statements: list[str] = []

        for table in Base.metadata.sorted_tables:
            schema = table.schema or "ulpin"
            present = actual.get((schema, table.name))
            if present is None:
                print(f"-- skipping {schema}.{table.name}: table does not exist")
                continue

            for column in table.columns:
                if column.name in present:
                    continue
                # compile() renders "name TYPE [NOT NULL] [DEFAULT ...]"; strip the
                # NOT NULL for the reason in the docstring.
                spec = str(CreateColumn(column).compile(dialect=dialect))
                spec = spec.replace(" NOT NULL", "")
                statements.append(
                    f"ALTER TABLE {schema}.{table.name} ADD COLUMN IF NOT EXISTS {spec};"
                )

        if not statements:
            print("-- nothing to add")
            await engine.dispose()
            return 0

        for s in statements:
            print(s)

        if apply:
            for s in statements:
                await conn.execute(text(s))
            await conn.commit()
            print(f"\n-- applied {len(statements)} statement(s)", file=sys.stderr)
        else:
            print(f"\n-- {len(statements)} statement(s); re-run with --apply", file=sys.stderr)

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
