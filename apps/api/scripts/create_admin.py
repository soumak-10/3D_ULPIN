"""Create or reset an administrator account.

The committed seed data in ``database/seeds/01_seed.sql`` carries placeholder
password hashes rather than real argon2id digests — a working hash cannot be
put in a public repository — so none of the seeded accounts can actually sign
in. This script closes that gap: it writes a real digest using the same
``hash_password`` the login path verifies with, so there is no way for the two
to disagree.

    python scripts/create_admin.py --email admin@ulpin.gov.in

Prompts for the password when ``--password`` is omitted, which keeps it out of
your shell history. Re-running against an existing address resets that account's
password, role and status instead of failing, so it doubles as a recovery tool
when someone locks themselves out.

Run from ``apps/api`` with the virtualenv active and ``.env`` pointing at the
database.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core import security
from app.db.session import dispose_engine, session_scope
from app.models.enums import UserRole, UserStatus
from app.models.user import User

# Roles that may hold national scope — ``ck_users_scope`` in 02_tables.sql
# rejects a NULL jurisdiction_code for anyone else.
NATIONAL_SCOPE_ROLES = {UserRole.ADMIN, UserRole.AUDITOR}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or reset an administrator account.",
        epilog="Omit --password to be prompted for it.",
    )
    parser.add_argument("--email", required=True, help="Sign-in address.")
    parser.add_argument("--password", help="Omit to be prompted (keeps it out of shell history).")
    parser.add_argument("--name", default="System Administrator", help="Display name.")
    parser.add_argument(
        "--role",
        default="ADMIN",
        choices=[r.value for r in UserRole],
        help="Defaults to ADMIN.",
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Jurisdiction code. Required for any role other than ADMIN or AUDITOR.",
    )
    return parser.parse_args()


def _resolve_password(supplied: str | None, email: str) -> str:
    password = supplied or getpass.getpass("Password: ")
    if not supplied:
        if password != getpass.getpass("Confirm: "):
            sys.exit("Passwords did not match.")

    # The same policy the register endpoint applies. Checking it here means a
    # password this script accepts is one the application would also accept —
    # otherwise you could bootstrap an account that fails its own rules.
    problems = security.validate_password_strength(password, email=email)
    if problems:
        print("Password rejected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        sys.exit(1)
    return password


async def _upsert(args: argparse.Namespace, password: str) -> None:
    email = args.email.strip()
    role = UserRole(args.role)

    if role not in NATIONAL_SCOPE_ROLES and not args.jurisdiction:
        sys.exit(f"--jurisdiction is required for {role.value}: only ADMIN and AUDITOR may hold national scope.")

    now = datetime.now(timezone.utc)
    digest = security.hash_password(password)

    async with session_scope() as session:
        existing = (
            await session.execute(
                select(User).where(
                    func.lower(User.email) == email.lower(),
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.password_hash = digest
            existing.password_changed_at = now
            existing.role = role
            existing.status = UserStatus.ACTIVE
            existing.email_verified = True
            existing.email_verified_at = now
            existing.jurisdiction_code = args.jurisdiction
            # Clear whatever locked the account out, and invalidate any tokens
            # issued before the reset — a password change that leaves old
            # sessions alive is not a password change.
            existing.failed_login_attempts = 0
            existing.locked_until = None
            existing.reset_token_hash = None
            existing.reset_token_expires_at = None
            existing.token_version += 1
            action = "reset"
        else:
            session.add(
                User(
                    email=email,
                    full_name=args.name,
                    password_hash=digest,
                    password_changed_at=now,
                    role=role,
                    status=UserStatus.ACTIVE,
                    jurisdiction_code=args.jurisdiction,
                    email_verified=True,
                    email_verified_at=now,
                )
            )
            action = "created"

    print(f"{action}: {email}  role={role.value}  status=ACTIVE")
    print("Sign in at http://localhost:3000/login")


async def _main() -> None:
    args = _parse_args()
    password = _resolve_password(args.password, args.email)
    try:
        await _upsert(args, password)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
