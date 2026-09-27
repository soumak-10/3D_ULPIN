"""Data access for ``users`` and ``refresh_tokens``.

All queries against these two tables live here. Services compose repositories and
own the transaction; repositories never commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole, UserStatus
from app.models.user import RefreshToken, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Reads ---------------------------------------------------------------
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.user_id == user_id, User.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup matching ``uq_users_email_lower``.

        Comparing ``lower(email)`` rather than ``email`` is what lets the partial
        unique index serve this query instead of a sequential scan.
        """
        stmt = select(User).where(
            func.lower(User.email) == email.strip().lower(),
            User.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        stmt = select(
            select(User.user_id)
            .where(func.lower(User.email) == email.strip().lower(), User.deleted_at.is_(None))
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar())

    async def get_by_reset_token_hash(self, token_hash: str) -> User | None:
        stmt = select(User).where(
            User.reset_token_hash == token_hash,
            User.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_verify_token_hash(self, token_hash: str) -> User | None:
        stmt = select(User).where(
            User.verify_token_hash == token_hash,
            User.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        jurisdiction_prefix: str | None = None,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        conditions = [User.deleted_at.is_(None)]
        if role is not None:
            conditions.append(User.role == role)
        if status is not None:
            conditions.append(User.status == status)
        if jurisdiction_prefix:
            # A parent jurisdiction contains its children: KA sees KA-BLR-001.
            conditions.append(
                or_(
                    User.jurisdiction_code == jurisdiction_prefix,
                    User.jurisdiction_code.like(f"{jurisdiction_prefix}-%"),
                )
            )
        if search:
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(User.full_name.ilike(pattern), User.email.ilike(pattern))
            )

        total = (
            await self.session.execute(
                select(func.count()).select_from(User).where(*conditions)
            )
        ).scalar_one()

        stmt = (
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, int(total)

    # -- Writes --------------------------------------------------------------
    def add(self, user: User) -> User:
        self.session.add(user)
        return user

    async def record_successful_login(self, user: User, ip: str | None) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        user.last_login_ip = ip
        await self.session.flush()

    async def record_failed_login(self, user: User) -> None:
        """Increment the counter and let the database decide about locking.

        ``fn_users_lockout`` in 04_triggers.sql applies the threshold. Deciding
        it here instead would let two API replicas each count to four and never
        lock the account.
        """
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        await self.session.flush()
        await self.session.refresh(user, ["locked_until", "failed_login_attempts", "status"])

    async def bump_token_version(self, user_id: uuid.UUID) -> None:
        await self.session.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(token_version=User.token_version + 1)
        )


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip_address: str | None = None,
        device_label: str | None = None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=(user_agent or "")[:400] or None,
            ip_address=ip_address,
            device_label=device_label,
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[RefreshToken]:
        stmt = (
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > func.now(),
            )
            .order_by(RefreshToken.issued_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def revoke(self, token: RefreshToken, reason: str) -> None:
        if token.revoked_at is None:
            token.revoked_at = datetime.now(UTC)
            token.revoked_reason = reason
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID, reason: str) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=func.now(), revoked_reason=reason)
        )
        return int(result.rowcount or 0)

    async def purge_expired(self, older_than: datetime) -> int:
        """Housekeeping for the nightly worker. Revoked rows are kept until they
        are well past expiry so reuse detection still has something to match."""
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < older_than)
        )
        return int(result.rowcount or 0)
