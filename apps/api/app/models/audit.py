"""Audit log: append-only, hash-chained, partitioned by month.

Read-mostly from the application's point of view. Rows are written by database
triggers using the ``app.current_user_id`` / ``app.current_user_role`` /
``app.request_id`` GUCs that :func:`app.db.session.set_audit_context` sets on
every authenticated request — so a write that bypasses the service layer is
still attributed, and a write with no principal is still recorded.

The ORM maps this table for *reading* (the dashboard's activity feed, forensic
search). ``UPDATE`` and ``DELETE`` are revoked from every application role, so
attempting either through the session is a permission error by design, not an
oversight.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, Boolean, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuditAction, UserRole

if TYPE_CHECKING:  # pragma: no cover
    pass

SCHEMA = "ulpin"


def _pg_enum(enum_cls: type, name: str) -> PGEnum:
    return PGEnum(
        enum_cls,
        name=name,
        schema=SCHEMA,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # created_at participates in the primary key because it is the partition
    # key, and PostgreSQL requires the partition key inside every unique
    # constraint on a partitioned table.
    log_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now(), nullable=False
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_role: Mapped[UserRole | None] = mapped_column(_pg_enum(UserRole, "user_role"))
    actor_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))

    action: Mapped[AuditAction] = mapped_column(
        _pg_enum(AuditAction, "audit_action"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    ulpin_code: Mapped[str | None] = mapped_column(String(32))

    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)
    changed_fields: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Tamper evidence: each row hashes its payload together with the previous
    # row's hash, so a retro-edit breaks the chain from that point forward and
    # the break is detectable without an external witness.
    row_hash: Mapped[bytes | None] = mapped_column()
    prev_hash: Mapped[bytes | None] = mapped_column()

    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_audit_actor", "actor_user_id", "created_at"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_ulpin", "ulpin_code"),
        {"schema": SCHEMA},
    )

    @property
    def summary(self) -> str:
        """One line for the dashboard's activity feed."""
        subject = self.ulpin_code or (str(self.entity_id)[:8] if self.entity_id else "—")
        return f"{self.action.value} {self.entity_type} {subject}"
