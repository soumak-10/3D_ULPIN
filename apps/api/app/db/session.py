"""Async engine, session factory and the request-scoped session dependency."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)

# The test suite runs each case inside a transaction that is rolled back, which
# a pooled connection would leak across. NullPool keeps that isolation honest.
_pool_kwargs: dict[str, Any] = (
    {"poolclass": NullPool}
    if settings.ENVIRONMENT == "test"
    else {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_pre_ping": True,
    }
)

engine: AsyncEngine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    echo=settings.DB_ECHO,
    future=True,
    connect_args={
        "server_settings": {
            "application_name": "ulpin-api",
            "search_path": f"{settings.POSTGRES_SCHEMA},public",
            # PostGIS function names resolve out of `public`; keeping it on the
            # path avoids schema-qualifying every ST_* call in raw SQL.
        },
        "timeout": 10,
    },
    **_pool_kwargs,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # response serialisation happens after commit
    autoflush=False,
    autobegin=True,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session bound to the request.

    The session is rolled back on any exception and always closed. Endpoints do
    not commit; services do. That keeps the transaction boundary in one layer.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Transactional scope for workers and CLI tasks, which have no request."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def set_audit_context(
    session: AsyncSession,
    *,
    user_id: str | None,
    user_role: str | None = None,
    request_id: str | None = None,
) -> None:
    """Publish the actor to the session's GUCs.

    ``fn_audit_capture()`` in 04_triggers.sql reads ``app.current_user_id``,
    ``app.current_user_role`` and ``app.request_id``. Without this call every
    audit row records a NULL actor, which defeats the point of the audit table.

    ``set_config(..., true)`` scopes the value to the current transaction, so it
    cannot leak to the next request that borrows the same pooled connection.
    """
    await session.execute(
        text(
            "SELECT set_config('app.current_user_id', :uid, true),"
            "       set_config('app.current_user_role', :role, true),"
            "       set_config('app.request_id', :rid, true)"
        ),
        {
            "uid": str(user_id) if user_id else "",
            "role": user_role or "",
            "rid": request_id or "",
        },
    )


async def check_database_health() -> dict[str, Any]:
    """Readiness probe: connectivity plus the extensions the domain depends on."""
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT current_database()                       AS database,
                       current_setting('server_version')        AS pg_version,
                       postgis_lib_version()                    AS postgis_version,
                       (SELECT count(*) FROM pg_extension
                         WHERE extname = 'postgis_sfcgal')      AS sfcgal
                """
            )
        )
        row = result.mappings().one()
        return {
            "status": "ok",
            "database": row["database"],
            "postgres": row["pg_version"],
            "postgis": row["postgis_version"],
            "sfcgal": bool(row["sfcgal"]),
        }


@event.listens_for(engine.sync_engine, "connect")
def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ANN401
    logger.debug("database connection established")


async def dispose_engine() -> None:
    await engine.dispose()
