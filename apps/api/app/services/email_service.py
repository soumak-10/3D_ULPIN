"""Transactional email.

Delivery is fire-and-forget by design: a user must not fail to register because
an SMTP relay is slow. Failures are logged and swallowed, and the account is
still created — the user can request another link.

When SMTP is unconfigured (the default locally) the message is written to the
log instead, including the link, so the flows are exercisable without a mail
server.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import quote

import anyio

from app.core.config import settings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


class EmailService:
    def __init__(self) -> None:
        self.enabled = settings.emails_enabled
        self.frontend = str(settings.FRONTEND_URL).rstrip("/")

    # -- Public API ----------------------------------------------------------
    async def send_verification(self, *, to: str, name: str, token: str) -> None:
        link = f"{self.frontend}/verify-email?token={quote(token)}"
        await self._send(
            to=to,
            subject="Confirm your ULPIN Registry account",
            body=_render(
                "verify_email",
                name=name,
                link=link,
                fallback=(
                    f"Hello {name},\n\n"
                    "Confirm your email address to activate your ULPIN Registry "
                    f"account:\n\n{link}\n\n"
                    f"The link expires in {settings.VERIFY_TOKEN_EXPIRE_HOURS} hours.\n"
                    "If you did not create this account, ignore this message."
                ),
            ),
        )

    async def send_password_reset(
        self, *, to: str, name: str, token: str, expires_minutes: int
    ) -> None:
        link = f"{self.frontend}/reset-password?token={quote(token)}"
        await self._send(
            to=to,
            subject="Reset your ULPIN Registry password",
            body=_render(
                "reset_password",
                name=name,
                link=link,
                fallback=(
                    f"Hello {name},\n\n"
                    f"Use this link to choose a new password:\n\n{link}\n\n"
                    f"It expires in {expires_minutes} minutes and can be used once.\n\n"
                    "If you did not request this, no action is needed — your "
                    "password has not changed."
                ),
            ),
        )

    async def send_password_changed(self, *, to: str, name: str) -> None:
        changed_at = datetime.now(UTC).strftime("%d %B %Y at %H:%M UTC")
        await self._send(
            to=to,
            subject="Your ULPIN Registry password was changed",
            body=_render(
                "password_changed",
                name=name,
                changed_at=changed_at,
                fallback=(
                    f"Hello {name},\n\n"
                    f"Your password was changed on {changed_at} and all other "
                    "sessions were signed out.\n\n"
                    "If this was not you, contact your registry office immediately."
                ),
            ),
        )

    # -- One-time passcodes --------------------------------------------------
    # The code is in the body, not a link. A passcode the user retypes proves
    # they can read the inbox without the registry having to host a URL that
    # performs a state change on GET — which email scanners and link previewers
    # will happily fetch on the user's behalf.
    async def send_verification_otp(self, *, to: str, name: str, code: str) -> None:
        await self._send(
            to=to,
            subject=f"{code} is your ULPIN Registry verification code",
            body=_render(
                "verify_email_otp",
                name=name,
                code=code,
                minutes=str(settings.OTP_EXPIRE_MINUTES),
                fallback=(
                    f"Hello {name},\n\n"
                    f"Your ULPIN Registry verification code is: {code}\n\n"
                    f"It is valid for {settings.OTP_EXPIRE_MINUTES} minutes and can "
                    "be used once.\n\n"
                    "If you did not create this account, ignore this message."
                ),
            ),
        )

    async def send_password_reset_otp(self, *, to: str, name: str, code: str) -> None:
        await self._send(
            to=to,
            subject=f"{code} is your ULPIN Registry password reset code",
            body=_render(
                "reset_password_otp",
                name=name,
                code=code,
                minutes=str(settings.OTP_EXPIRE_MINUTES),
                fallback=(
                    f"Hello {name},\n\n"
                    f"Your password reset code is: {code}\n\n"
                    f"It is valid for {settings.OTP_EXPIRE_MINUTES} minutes and can "
                    "be used once.\n\n"
                    "If you did not request this, no action is needed — your "
                    "password has not changed."
                ),
            ),
        )

    async def send_invitation(self, *, to: str, name: str, token: str, role: str) -> None:
        link = f"{self.frontend}/reset-password?token={quote(token)}&invite=1"
        await self._send(
            to=to,
            subject="You have been invited to the ULPIN Registry",
            body=(
                f"Hello {name},\n\n"
                f"An account has been created for you with the role {role}.\n\n"
                f"Set your password to get started:\n\n{link}\n\n"
                f"The link expires in {settings.VERIFY_TOKEN_EXPIRE_HOURS} hours."
            ),
        )

    # -- Transport -----------------------------------------------------------
    async def _send(self, *, to: str, subject: str, body: str) -> None:
        if not self.enabled:
            logger.info(
                "[email disabled] to=%s subject=%r\n%s", to, subject, body
            )
            return
        try:
            # smtplib is blocking; a worker thread keeps the event loop free.
            await anyio.to_thread.run_sync(self._send_sync, to, subject, body)
        except Exception:  # noqa: BLE001 — delivery must never fail the request
            logger.exception("email delivery failed to=%s subject=%r", to, subject)

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_FROM))
        message["To"] = to
        message.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_TLS:
                smtp.starttls(context=context)
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD.get_secret_value())
            smtp.send_message(message)
        logger.info("email sent to=%s subject=%r", to, subject)


def _render(template: str, *, fallback: str, **context: str) -> str:
    """Substitute into ``templates/email/<template>.txt`` if it exists.

    Templates are optional; the inline fallback keeps every flow working in a
    fresh checkout before anyone writes the branded versions.
    """
    path = TEMPLATE_DIR / f"{template}.txt"
    if not path.exists():
        return fallback
    try:
        return path.read_text(encoding="utf-8").format(**context)
    except (KeyError, OSError):
        logger.warning("email template %s is unusable; falling back", template)
        return fallback
