"""Password hashing, token minting and verification.

Deliberate choices:

* **Argon2id** for passwords — memory-hard, and the reference recommendation.
  ``needs_rehash`` lets parameters be raised later without a mass reset.
* **Opaque refresh tokens**, not JWTs. A refresh token must be revocable the
  instant it is used twice; a stateless JWT cannot be. Only the SHA-256 of the
  token is stored, so a database leak does not yield usable sessions.
* **``token_version`` inside the access token.** Bumping the user's counter
  invalidates every outstanding access token without a blocklist.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.core.config import settings
from app.core.exceptions import InvalidTokenException, TokenExpiredException

TokenType = Literal["access", "refresh", "reset", "verify"]

_hasher: Final = PasswordHasher(
    time_cost=settings.ARGON2_TIME_COST,
    memory_cost=settings.ARGON2_MEMORY_COST,
    parallelism=settings.ARGON2_PARALLELISM,
    hash_len=settings.ARGON2_HASH_LEN,
    salt_len=settings.ARGON2_SALT_LEN,
)

# Verified against a throwaway hash when the user does not exist, so a missing
# account and a wrong password take the same wall-clock time. Without this, login
# latency alone enumerates valid email addresses.
_DUMMY_HASH: Final[str] = _hasher.hash("timing-attack-mitigation-placeholder")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Constant-ish time password check.

    Passing ``None`` still performs a full verification against the dummy hash,
    which is what keeps non-existent accounts indistinguishable from wrong
    passwords.
    """
    target = hashed or _DUMMY_HASH
    try:
        _hasher.verify(target, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return hashed is not None


def password_needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------
def create_access_token(
    *,
    subject: uuid.UUID | str,
    role: str,
    token_version: int,
    jurisdiction_code: str | None = None,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))

    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "ver": token_version,
        "jur": jurisdiction_code,
        "type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate. Raises the domain exceptions, never jwt's."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredException("Access token has expired") from exc
    except InvalidTokenError as exc:
        raise InvalidTokenException(f"Access token is invalid: {exc}") from exc

    if payload.get("type") != "access":
        # A refresh or reset token presented as a bearer credential.
        raise InvalidTokenException("Token is not an access token")

    return payload


# ---------------------------------------------------------------------------
# Opaque tokens: refresh, password reset, email verification
# ---------------------------------------------------------------------------
def generate_opaque_token(nbytes: int = 48) -> str:
    """Return a URL-safe random token. This value is shown to the user once."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest, for storage and lookup.

    A plain digest is correct here: the input is already 384 bits of entropy, so
    there is nothing for a slow KDF to defend against, and reset lookups must
    stay index-friendly.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(presented: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_token(presented), stored_hash)


# ---------------------------------------------------------------------------
# One-time passcodes
# ---------------------------------------------------------------------------
def generate_otp(length: int | None = None) -> str:
    """A numeric passcode the user retypes from their inbox.

    ``secrets.randbelow`` rather than ``random``: the sequence must not be
    predictable from an earlier code. Zero-padded, so every code is the declared
    length and "012345" is a valid one — trimming leading zeros would quietly
    shrink the space by a tenth.
    """
    digits = length or settings.OTP_LENGTH
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def hash_otp(user_id: uuid.UUID | str, code: str) -> str:
    """Digest of ``user_id:code``.

    Six digits is 10^6, so this digest is not a meaningful barrier to an
    attacker who has the table and time — expiry, the attempt cap and single use
    are what stop guessing. It is here so the code is not sitting in plaintext
    in a backup or a support query, and the user_id prefix means one precomputed
    table of digests does not unlock every row at once.
    """
    return hashlib.sha256(f"{user_id}:{code.strip()}".encode()).hexdigest()


def otps_match(presented: str, stored_hash: str | None, *, user_id: uuid.UUID | str) -> bool:
    """Constant-time comparison. Never short-circuits on the first wrong digit."""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_otp(user_id, presented), stored_hash)


def otp_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)


def reset_ticket_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=settings.RESET_TICKET_EXPIRE_MINUTES)


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def reset_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES)


def verify_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=settings.VERIFY_TOKEN_EXPIRE_HOURS)


# ---------------------------------------------------------------------------
# Statutory identifier hashing
# ---------------------------------------------------------------------------
def hash_national_id(id_type: str, id_value: str) -> bytes:
    """HMAC a statutory ID under the server pepper.

    The raw number is never persisted. HMAC rather than a plain digest so the
    pepper is a real key: without it, the 12-digit Aadhaar space is trivially
    enumerable by brute force.
    """
    normalised = f"{id_type.upper()}:{id_value.strip().replace(' ', '')}"
    return hmac.new(
        settings.ID_HASH_PEPPER.get_secret_value().encode("utf-8"),
        normalised.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def national_id_last4(id_value: str) -> str:
    digits = id_value.strip().replace(" ", "")
    return digits[-4:] if len(digits) >= 4 else digits.rjust(4, "*")


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------
_COMMON_PASSWORDS: Final[frozenset[str]] = frozenset(
    {
        "password", "123456", "12345678", "qwerty", "abc123", "111111",
        "password1", "admin", "welcome", "letmein", "iloveyou", "monkey",
        "dragon", "sunshine", "princess", "football", "charlie", "aa123456",
        "password123", "qwerty123", "1q2w3e4r", "admin123", "welcome123",
    }
)


def validate_password_strength(password: str, *, email: str | None = None) -> list[str]:
    """Return a list of policy violations. Empty list means the password passes."""
    problems: list[str] = []

    if len(password) < settings.PASSWORD_MIN_LENGTH:
        problems.append(f"Must be at least {settings.PASSWORD_MIN_LENGTH} characters")
    if len(password) > 128:
        problems.append("Must be at most 128 characters")
    if not any(c.islower() for c in password):
        problems.append("Must contain a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("Must contain an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("Must contain a digit")
    if not any(not c.isalnum() for c in password):
        problems.append("Must contain a symbol")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("Is among the most commonly used passwords")
    if email and email.split("@")[0].lower() in password.lower():
        problems.append("Must not contain your email address")

    return problems
