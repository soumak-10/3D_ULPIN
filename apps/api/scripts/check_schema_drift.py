"""Compare every SQLAlchemy model against the columns that actually exist.

The SQL schema in database/schemas/ and the ORM in app/models/ are maintained by
hand and independently, so a column can be declared in one and missing from the
other. That mismatch is invisible until a query touches the column at runtime,
and SELECT emits every mapped column, so the failure surfaces on an unrelated
request with a confusing message.

Run it after any schema change:

    .venv/Scripts/python.exe scripts/check_schema_drift.py

Exits non-zero if anything is out of step, so it can gate a deploy.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.base import Base
from app.db.session import engine

# Importing the package registers every model on Base.metadata; without this the
# registry is empty and the check silently passes.
import app.models  # noqa: F401


async def main() -> int:
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

    problems = 0
    for table in Base.metadata.sorted_tables:
        schema = table.schema or "ulpin"
        present = actual.get((schema, table.name))
        if present is None:
            print(f"MISSING TABLE  {schema}.{table.name}")
            problems += 1
            continue

        mapped = {c.name for c in table.columns}
        only_in_model = sorted(mapped - present)
        only_in_db = sorted(present - mapped)

        if only_in_model:
            print(f"{schema}.{table.name}: in model, not in database -> {', '.join(only_in_model)}")
            problems += 1
        if only_in_db:
            # Not an error on its own: the SQL schema may carry columns the API
            # has no reason to map. Reported so the difference is a decision
            # rather than an accident.
            print(f"{schema}.{table.name}: in database, not mapped     -> {', '.join(only_in_db)}")

    await engine.dispose()

    if problems:
        print(f"\n{problems} table(s) would fail at runtime.")
        return 1
    print("\nEvery mapped column exists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
