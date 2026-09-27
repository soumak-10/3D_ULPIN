"""Authentication use cases.

This is the only layer that opens transactions. Endpoints translate HTTP to
these calls and back; repositories do not commit.

Two behaviours here are deliberate and easy to get wrong:

* **Silent success on forgot-password.** The endpoint returns the same response
  whether or not the address exists. Returning "no such user" turns the reset
  form into an account enumeration oracle.
* **Refresh rotation with reuse detection.** Every refresh mints a new token and
  revokes the old one. If a token that was already rotated comes back, the
  presenter is holding a stolen copy — the entire family is revoked and the
  user's ``token_version`` is bumped, killing every live access token too.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.core.exceptions import (
    AccountInactiveException,
    AccountLockedException,
    ConflictException,
    DuplicateEmailException,
    EmailNotVerifiedException,
    InvalidCredentialsException,
    InvalidTokenException,
    NotFoundException,
    PermissionDeniedException,
    RateLimitException,
    TokenReuseException,
    WeakPasswordException,
)
from app.core.permissions import Role
from app.models.enums import UserRole, UserStatus
from app.models.user import OtpPurpose, RefreshToken, User
from app.repositories.user_repository import RefreshTokenRepository, UserRepository
from app.schemas.auth import (
    AdminCreateUserRequest,
    LoginRequest,
    RegisterRequest,
)
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

# One wording for every OTP refusal that must not distinguish "no such account"
# from "wrong code".
_OTP_REJECTED = "That code is not valid. Request a new one."


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.email = EmailService()

    # =======================================================================
    # Registration
    # =======================================================================
    async def register(
        self,
        payload: RegisterRequest,
        *,
        ip: str | None = None,
    ) -> tuple[User, str]:
        """Create a self-service account. Returns the user and the raw
        verification code (the caller mails it; only its digest is persisted)."""
        email = str(payload.email).strip().lower()

        if await self.users.email_exists(email):
            # Registration must tell the truth here — the user has to know the
            # address is taken. The enumeration risk is instead mitigated by
            # rate limiting this endpoint.
            raise DuplicateEmailException(f"An account already exists for {email}")

        problems = security.validate_password_strength(payload.password, email=email)
        if problems:
            raise WeakPasswordException(
                "Password does not meet policy",
                errors={"password": problems},
            )

        user = User(
            email=email,
            full_name=payload.full_name,
            phone=payload.phone,
            password_hash=security.hash_password(payload.password),
            role=UserRole(payload.role.value),
            status=UserStatus.PENDING,
            jurisdiction_code=payload.jurisdiction_code,
            email_verified=False,
        )
        self.users.add(user)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            # Only claim the address is taken when the email index is what
            # actually rejected the row. Blaming every IntegrityError on the
            # email sends the user off to try a different address while the
            # real cause — a check constraint, a missing foreign key — stays
            # invisible in a 409 that looks entirely plausible.
            constraint = getattr(getattr(exc, "orig", None), "constraint_name", "") or str(exc)
            if "email" in constraint.lower():
                raise DuplicateEmailException(
                    f"An account already exists for {email}"
                ) from exc
            logger.exception("registration rejected by the database: %s", constraint)
            raise

        # The code is bound to user_id, so it can only be minted once the row has
        # one — hence here rather than in the constructor above.
        code = self._arm_otp(user, OtpPurpose.EMAIL_VERIFY)

        await self.session.commit()
        await self.session.refresh(user)

        await self.email.send_verification_otp(
            to=user.email, name=user.full_name, code=code
        )
        logger.info("user registered user_id=%s role=%s ip=%s", user.user_id, user.role, ip)
        return user, code

    async def admin_create_user(
        self,
        payload: AdminCreateUserRequest,
        *,
        actor: User,
    ) -> tuple[User, str | None]:
        """Provision an account with an arbitrary role.

        A PROPERTY_OFFICER may create citizen accounts inside their own
        jurisdiction; only an ADMIN may mint officers, auditors or admins.
        """
        privileged = {UserRole.ADMIN, UserRole.PROPERTY_OFFICER, UserRole.AUDITOR, UserRole.SERVICE}
        if payload.role in privileged and actor.role is not UserRole.ADMIN:
            raise PermissionDeniedException(
                f"Only an administrator may create {payload.role.value} accounts"
            )

        email = str(payload.email).strip().lower()
        if await self.users.email_exists(email):
            raise DuplicateEmailException(f"An account already exists for {email}")

        raw_invite: str | None = None
        if payload.password:
            problems = security.validate_password_strength(payload.password, email=email)
            if problems:
                raise WeakPasswordException(
                    "Password does not meet policy", errors={"password": problems}
                )
            password_hash = security.hash_password(payload.password)
            status = UserStatus.ACTIVE
        else:
            # No password set: the account is unusable until the invitee picks
            # one. A random unguessable hash is stored so the column is never
            # empty and no "blank password" path can ever authenticate.
            password_hash = security.hash_password(security.generate_opaque_token())
            status = UserStatus.PENDING
            raw_invite = security.generate_opaque_token()

        user = User(
            email=email,
            full_name=payload.full_name,
            phone=payload.phone,
            password_hash=password_hash,
            role=payload.role,
            status=status,
            jurisdiction_code=payload.jurisdiction_code,
            email_verified=bool(payload.password),
            email_verified_at=datetime.now(UTC) if payload.password else None,
        )
        if raw_invite:
            user.reset_token_hash = security.hash_token(raw_invite)
            user.reset_token_expires_at = security.verify_token_expiry()

        self.users.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateEmailException(f"An account already exists for {email}") from exc

        await self.session.commit()
        await self.session.refresh(user)

        if raw_invite and payload.send_invite:
            await self.email.send_invitation(
                to=user.email, name=user.full_name, token=raw_invite, role=user.role.value
            )
        logger.info(
            "user provisioned user_id=%s role=%s by=%s", user.user_id, user.role, actor.user_id
        )
        return user, raw_invite

    # =======================================================================
    # Login
    # =======================================================================
    async def authenticate(
        self,
        payload: LoginRequest,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        """Verify credentials and issue a token pair.

        Returns ``(user, access_token, raw_refresh_token)``.
        """
        email = str(payload.email).strip().lower()
        user = await self.users.get_by_email(email)

        # Runs even when the user is absent: the dummy-hash path keeps the
        # response time of "no such account" indistinguishable from "wrong
        # password". Skipping it would leak valid addresses through latency.
        password_ok = security.verify_password(payload.password, user.password_hash if user else None)

        if user is None or not password_ok:
            if user is not None:
                await self.users.record_failed_login(user)
                await self.session.commit()
                if user.is_locked:
                    raise AccountLockedException(
                        "Too many failed attempts. This account is locked for "
                        f"{settings.LOCKOUT_MINUTES} minutes."
                    )
            raise InvalidCredentialsException()

        if user.is_locked:
            remaining = int((user.locked_until - datetime.now(UTC)).total_seconds() // 60) + 1  # type: ignore[operator]
            raise AccountLockedException(
                f"This account is locked. Try again in {remaining} minute(s)."
            )

        if user.status in (UserStatus.SUSPENDED, UserStatus.DEACTIVATED):
            raise AccountInactiveException(
                f"This account is {user.status.value.lower()}. Contact your administrator."
            )

        # An unconfirmed address cannot sign in. This is a deliberate reversal of
        # the earlier behaviour, which let PENDING accounts through so the UI
        # could show a banner: registration is now open to the public, so an
        # unverified account is an unproven claim on someone else's mailbox.
        #
        # The check is on email_verified rather than status because an account
        # can be ACTIVE for administrative reasons while its address is still
        # unproven. Gated by a setting so a stalled SMTP relay is a config change
        # rather than a deploy.
        if settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
            logger.info("login refused, email unverified user_id=%s", user.user_id)
            raise EmailNotVerifiedException("Please verify your email before login")

        if security.password_needs_rehash(user.password_hash):
            # Argon2 parameters were raised since this hash was written. Upgrade
            # it now, while the plaintext is legitimately in hand.
            user.password_hash = security.hash_password(payload.password)

        await self.users.record_successful_login(user, ip)

        access = self._mint_access_token(user)
        raw_refresh = await self._issue_refresh_token(
            user,
            user_agent=user_agent,
            ip=ip,
            remember=payload.remember_me,
        )
        await self.session.commit()

        logger.info("login ok user_id=%s role=%s ip=%s", user.user_id, user.role, ip)
        return user, access, raw_refresh

    # =======================================================================
    # Refresh rotation
    # =======================================================================
    async def refresh(
        self,
        raw_token: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        token_hash = security.hash_token(raw_token)
        stored = await self.tokens.get_by_hash(token_hash)

        if stored is None:
            raise InvalidTokenException("Refresh token is not recognised")

        if stored.revoked_at is not None:
            # A revoked token came back. Either it was rotated (and the holder
            # kept a copy) or it was explicitly revoked and is now being
            # replayed. Both mean the token is compromised: burn the account's
            # sessions rather than merely refusing this one request.
            await self.tokens.revoke_all_for_user(stored.user_id, "reuse_detected")
            await self.users.bump_token_version(stored.user_id)
            await self.session.commit()
            logger.warning(
                "refresh token reuse detected user_id=%s token_id=%s ip=%s",
                stored.user_id,
                stored.token_id,
                ip,
            )
            raise TokenReuseException()

        if stored.expires_at <= datetime.now(UTC):
            await self.tokens.revoke(stored, "expired")
            await self.session.commit()
            raise InvalidTokenException("Refresh token has expired")

        user = await self.users.get_by_id(stored.user_id)
        if user is None or not user.can_authenticate:
            await self.tokens.revoke(stored, "account_unavailable")
            await self.session.commit()
            raise AccountInactiveException("This account can no longer sign in")

        new_raw = await self._issue_refresh_token(
            user, user_agent=user_agent, ip=ip, remember=True
        )
        new_hash = security.hash_token(new_raw)
        replacement = await self.tokens.get_by_hash(new_hash)

        stored.revoked_at = datetime.now(UTC)
        stored.revoked_reason = "rotated"
        stored.replaced_by = replacement.token_id if replacement else None
        stored.last_used_at = datetime.now(UTC)

        access = self._mint_access_token(user)
        await self.session.commit()
        return user, access, new_raw

    # =======================================================================
    # Logout
    # =======================================================================
    async def logout(self, raw_token: str | None, *, all_devices: bool = False) -> int:
        """Revoke the presented session, or every session for its owner.

        Returns the number of sessions revoked. Logging out with no valid token
        is not an error — the client's intent is already satisfied.
        """
        if not raw_token:
            return 0

        stored = await self.tokens.get_by_hash(security.hash_token(raw_token))
        if stored is None:
            return 0

        if all_devices:
            count = await self.tokens.revoke_all_for_user(stored.user_id, "logout_all")
            # Access tokens are stateless; only a version bump ends them early.
            await self.users.bump_token_version(stored.user_id)
        else:
            await self.tokens.revoke(stored, "logout")
            count = 1

        await self.session.commit()
        return count

    async def logout_user(self, user_id: uuid.UUID, reason: str = "admin_revoke") -> int:
        count = await self.tokens.revoke_all_for_user(user_id, reason)
        await self.users.bump_token_version(user_id)
        await self.session.commit()
        return count

    # =======================================================================
    # Password reset
    # =======================================================================
    async def request_password_reset(self, email: str, *, ip: str | None = None) -> str | None:
        """Issue a reset token if the address is known.

        Returns the raw token for tests and local development. The endpoint
        discards it and always reports success — the caller must never learn
        whether the address exists.
        """
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or user.deleted_at is not None:
            logger.info("password reset requested for unknown address ip=%s", ip)
            return None

        if user.status is UserStatus.DEACTIVATED:
            return None

        # Throttle: a fresh token within the cooldown window replaces nothing,
        # so repeated clicks cannot be used to flood the user's inbox.
        now = datetime.now(UTC)
        if user.reset_requested_at and (now - user.reset_requested_at).total_seconds() < 60:
            logger.info("password reset throttled user_id=%s", user.user_id)
            return None

        raw = security.generate_opaque_token()
        user.reset_token_hash = security.hash_token(raw)
        user.reset_token_expires_at = security.reset_token_expiry()
        user.reset_requested_at = now
        await self.session.commit()

        await self.email.send_password_reset(
            to=user.email,
            name=user.full_name,
            token=raw,
            expires_minutes=settings.RESET_TOKEN_EXPIRE_MINUTES,
        )
        logger.info("password reset issued user_id=%s ip=%s", user.user_id, ip)
        return raw

    async def reset_password(self, raw_token: str, new_password: str) -> User:
        user = await self.users.get_by_reset_token_hash(security.hash_token(raw_token))

        if user is None or not user.reset_token_is_valid():
            raise InvalidTokenException("This reset link is invalid or has expired")

        problems = security.validate_password_strength(new_password, email=user.email)
        if problems:
            raise WeakPasswordException(
                "Password does not meet policy", errors={"password": problems}
            )

        if security.verify_password(new_password, user.password_hash):
            raise ConflictException("New password must differ from the current one")

        user.password_hash = security.hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        user.reset_token_hash = None
        user.reset_token_expires_at = None
        user.failed_login_attempts = 0
        user.locked_until = None

        # Anyone resetting a password may be recovering from a compromise, so
        # every existing session dies. The password-rotation trigger also bumps
        # token_version; doing it here keeps the behaviour explicit for readers
        # and correct if the trigger is ever dropped.
        await self.tokens.revoke_all_for_user(user.user_id, "password_reset")
        user.token_version = (user.token_version or 0) + 1

        if user.status is UserStatus.PENDING and not user.email_verified:
            # Completing a reset proves control of the mailbox.
            user.email_verified = True
            user.email_verified_at = datetime.now(UTC)
            user.status = UserStatus.ACTIVE

        await self.session.commit()
        await self.session.refresh(user)

        await self.email.send_password_changed(to=user.email, name=user.full_name)
        logger.info("password reset completed user_id=%s", user.user_id)
        return user

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> User:
        if not security.verify_password(current_password, user.password_hash):
            raise InvalidCredentialsException("Current password is incorrect")

        problems = security.validate_password_strength(new_password, email=user.email)
        if problems:
            raise WeakPasswordException(
                "Password does not meet policy", errors={"password": problems}
            )

        user.password_hash = security.hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        user.token_version = (user.token_version or 0) + 1
        await self.tokens.revoke_all_for_user(user.user_id, "password_changed")
        await self.session.commit()
        await self.session.refresh(user)

        await self.email.send_password_changed(to=user.email, name=user.full_name)
        return user

    # =======================================================================
    # Email verification
    # =======================================================================
    async def verify_email(self, raw_token: str) -> User:
        user = await self.users.get_by_verify_token_hash(security.hash_token(raw_token))
        if user is None or not user.verify_token_is_valid():
            raise InvalidTokenException("This verification link is invalid or has expired")

        user.email_verified = True
        user.email_verified_at = datetime.now(UTC)
        user.verify_token_hash = None
        user.verify_token_expires_at = None
        if user.status is UserStatus.PENDING:
            user.status = UserStatus.ACTIVE

        await self.session.commit()
        await self.session.refresh(user)
        logger.info("email verified user_id=%s", user.user_id)
        return user

    async def resend_verification(self, email: str) -> None:
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or user.email_verified:
            return  # silent: same response shape either way

        raw = security.generate_opaque_token()
        user.verify_token_hash = security.hash_token(raw)
        user.verify_token_expires_at = security.verify_token_expiry()
        await self.session.commit()

        await self.email.send_verification(to=user.email, name=user.full_name, token=raw)

    # =======================================================================
    # One-time passcodes
    # =======================================================================
    # Registration and recovery both run on six-digit codes. The guessing
    # defence is not the digest in the database — six digits fall instantly to
    # an offline attack — it is the combination of a ten-minute life, a
    # five-attempt cap that burns the challenge, and single use.
    async def verify_email_otp(self, email: str, code: str) -> User:
        """``POST /auth/verify-otp``. Confirms the address and activates the
        account."""
        user = await self.users.get_by_email(email.strip().lower())

        # A missing account and a wrong code fail identically. The registration
        # endpoint is where this system admits an address is taken; this one does
        # not need to be a second oracle.
        if user is None or user.deleted_at is not None:
            raise InvalidTokenException(_OTP_REJECTED)

        if user.email_verified:
            # Idempotent: a double submit, or a second tab, should land on the
            # signed-in state rather than an error the user cannot act on.
            return user

        if not user.otp_is_live():
            raise InvalidTokenException(
                "That code has expired or has been used. Request a new one."
            )

        if not security.otps_match(code, user.otp_code_hash, user_id=user.user_id):
            raise await self._record_bad_guess(user, OtpPurpose.EMAIL_VERIFY)

        user.email_verified = True
        user.email_verified_at = datetime.now(UTC)
        user.clear_otp()
        if user.status is UserStatus.PENDING:
            user.status = UserStatus.ACTIVE
        # Any outstanding link token is moot now.
        user.verify_token_hash = None
        user.verify_token_expires_at = None

        await self.session.commit()
        await self.session.refresh(user)
        logger.info("email verified by otp user_id=%s", user.user_id)
        return user

    async def resend_otp(self, email: str, purpose: OtpPurpose) -> None:
        """``POST /auth/resend-otp``. Issues a fresh code, invalidating the
        previous one."""
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or user.deleted_at is not None:
            return
        if user.status is UserStatus.DEACTIVATED:
            return

        if purpose is OtpPurpose.EMAIL_VERIFY:
            if user.email_verified:
                return  # nothing to prove
            code = self._arm_otp(user, purpose)
            await self.session.commit()
            await self.email.send_verification_otp(
                to=user.email, name=user.full_name, code=code
            )
        else:
            await self.request_password_reset_otp(email)
        logger.info("otp resent user_id=%s purpose=%s", user.user_id, purpose.value)

    async def request_password_reset_otp(
        self, email: str, *, ip: str | None = None
    ) -> str | None:
        """``POST /auth/forgot-password``. Returns the raw code for tests and
        local development; the endpoint discards it and always reports success."""
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or user.deleted_at is not None:
            logger.info("password reset requested for unknown address ip=%s", ip)
            return None
        if user.status is UserStatus.DEACTIVATED:
            return None

        try:
            code = self._arm_otp(user, OtpPurpose.PASSWORD_RESET)
        except RateLimitException:
            # Swallowed rather than raised. The caller of this flow is
            # unauthenticated, so a 429 here — against a silent 200 for an
            # unknown address — would tell an attacker which addresses are
            # registered. The throttle still did its job: no mail goes out.
            logger.info("password reset otp throttled user_id=%s", user.user_id)
            return None

        user.reset_requested_at = datetime.now(UTC)
        await self.session.commit()

        await self.email.send_password_reset_otp(
            to=user.email, name=user.full_name, code=code
        )
        logger.info("password reset otp issued user_id=%s ip=%s", user.user_id, ip)
        return code

    async def verify_reset_otp(self, email: str, code: str) -> str:
        """``POST /auth/verify-reset-otp``. Spends the code and returns the
        ticket ``/auth/reset-password`` accepts.

        Splitting verification from the password change is what makes "invalidate
        the OTP after use" true: by the time the new password is submitted the
        code is already gone, so a code observed in transit cannot be replayed
        against the reset endpoint.
        """
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or user.deleted_at is not None:
            raise InvalidTokenException(_OTP_REJECTED)

        if not user.reset_otp_is_live():
            raise InvalidTokenException(
                "That code has expired or has been used. Request a new one."
            )

        if not security.otps_match(code, user.reset_otp_hash, user_id=user.user_id):
            raise await self._record_bad_guess(user, OtpPurpose.PASSWORD_RESET)

        user.clear_reset_otp()
        ticket = security.generate_opaque_token()
        user.reset_token_hash = security.hash_token(ticket)
        user.reset_token_expires_at = security.reset_ticket_expiry()
        await self.session.commit()

        logger.info("reset otp verified user_id=%s", user.user_id)
        return ticket

    # -- OTP internals -------------------------------------------------------
    def _arm_otp(self, user: User, purpose: OtpPurpose) -> str:
        """Mint a code, overwrite any previous one for that purpose, and count
        the send. Raises :class:`RateLimitException` if the caller is asking too
        often. Does not commit — the caller owns the transaction."""
        self._guard_send_rate(user)
        code = security.generate_otp()
        digest = security.hash_otp(user.user_id, code)
        expires = security.otp_expiry()

        if purpose is OtpPurpose.EMAIL_VERIFY:
            user.otp_code_hash = digest
            user.otp_expires_at = expires
            user.otp_attempts = 0
        else:
            user.reset_otp_hash = digest
            user.reset_otp_expires_at = expires
            user.reset_otp_attempts = 0

        now = datetime.now(UTC)
        user.otp_sent_at = now
        user.otp_send_count = (user.otp_send_count or 0) + 1
        return code

    def _guard_send_rate(self, user: User) -> None:
        """Two limits, because they stop different things.

        The cooldown stops a held-down button. The hourly cap stops a patient
        script from turning this account into a mail bomb aimed at someone else's
        inbox — the victim of OTP flooding is the address owner, not the service.
        """
        now = datetime.now(UTC)

        if user.otp_sent_at is not None:
            elapsed = (now - user.otp_sent_at).total_seconds()
            if elapsed < settings.OTP_RESEND_COOLDOWN_SECONDS:
                wait = int(settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed) + 1
                raise RateLimitException(
                    f"Please wait {wait} second(s) before requesting another code",
                    retry_after=wait,
                )

        # Rolling window anchored on first send, so the count clears itself
        # without a scheduled job.
        window = user.otp_window_started_at
        if window is None or (now - window) >= timedelta(hours=1):
            user.otp_window_started_at = now
            user.otp_send_count = 0

        if (user.otp_send_count or 0) >= settings.OTP_MAX_SENDS_PER_HOUR:
            raise RateLimitException(
                "Too many codes requested for this account. Try again in an hour.",
                retry_after=3600,
            )

    async def _record_bad_guess(
        self, user: User, purpose: OtpPurpose
    ) -> InvalidTokenException:
        """Count a wrong guess, burning the challenge once the cap is reached.

        Returns the exception for the caller to ``raise`` so the commit and the
        raise stay adjacent. The counter is committed even though the request
        fails — a rolled-back attempt counter is not a counter.
        """
        if purpose is OtpPurpose.EMAIL_VERIFY:
            user.otp_attempts += 1
            remaining = settings.OTP_MAX_ATTEMPTS - user.otp_attempts
            if remaining <= 0:
                user.clear_otp()
        else:
            user.reset_otp_attempts += 1
            remaining = settings.OTP_MAX_ATTEMPTS - user.reset_otp_attempts
            if remaining <= 0:
                user.clear_reset_otp()

        await self.session.commit()
        logger.info(
            "otp rejected user_id=%s purpose=%s remaining=%s",
            user.user_id,
            purpose.value,
            max(remaining, 0),
        )

        if remaining <= 0:
            return InvalidTokenException(
                "Too many incorrect attempts. That code is no longer valid — "
                "request a new one."
            )
        return InvalidTokenException(
            f"Incorrect code. {remaining} attempt(s) remaining."
        )


    # =======================================================================
    # Sessions
    # =======================================================================
    async def list_sessions(
        self, user_id: uuid.UUID, *, current_hash: str | None = None
    ) -> list[tuple[RefreshToken, bool]]:
        rows = await self.tokens.list_active_for_user(user_id)
        return [(r, r.token_hash == current_hash) for r in rows]

    async def revoke_session(self, user: User, token_id: uuid.UUID) -> None:
        stored = await self.session.get(RefreshToken, token_id)
        if stored is None or stored.user_id != user.user_id:
            # Do not reveal that another user's session exists.
            raise NotFoundException("Session", token_id)
        await self.tokens.revoke(stored, "revoked_by_user")
        await self.session.commit()

    # =======================================================================
    # Internals
    # =======================================================================
    def _mint_access_token(self, user: User) -> str:
        return security.create_access_token(
            subject=user.user_id,
            role=user.role.value,
            token_version=user.token_version or 0,
            jurisdiction_code=user.jurisdiction_code,
        )

    async def _issue_refresh_token(
        self,
        user: User,
        *,
        user_agent: str | None,
        ip: str | None,
        remember: bool,
    ) -> str:
        raw = security.generate_opaque_token()
        # "Remember me" buys the full refresh window; without it the session
        # lasts a day, which is long enough to survive a page reload and short
        # enough to matter on a shared machine.
        expires = (
            security.refresh_token_expiry()
            if remember
            else datetime.now(UTC) + timedelta(days=1)
        )
        await self.tokens.create(
            user_id=user.user_id,
            token_hash=security.hash_token(raw),
            expires_at=expires,
            user_agent=user_agent,
            ip_address=ip,
            device_label=_device_label(user_agent),
        )
        return raw


def _device_label(user_agent: str | None) -> str | None:
    """Coarse device description for the session list. Not security-relevant —
    it exists so a user can recognise their own devices."""
    if not user_agent:
        return None
    ua = user_agent.lower()
    if "android" in ua:
        platform = "Android"
    elif "iphone" in ua or "ipad" in ua:
        platform = "iOS"
    elif "windows" in ua:
        platform = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        platform = "macOS"
    elif "linux" in ua:
        platform = "Linux"
    else:
        platform = "Unknown"

    if "edg/" in ua:
        browser = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} on {platform}"


__all__ = ["AuthService", "Role"]
