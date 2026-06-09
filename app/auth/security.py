"""Auth primitives: password hashing + JWT access/refresh tokens.

Pure functions (no DB, no app) so they unit-test in isolation. Implements the
Adaptive Gateway's access + refresh-rotation pattern; the refresh ``jti`` is
tracked in Postgres (see ``RefreshToken``) so rotation can revoke the prior
token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class AuthPrincipal:
    """The identity attached to ``request.state`` by ``AuthMiddleware``."""

    user_id: str
    role: str


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def _encode(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra: dict | None = None,
) -> tuple[str, str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    jti = str(uuid.uuid4())
    payload: dict = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": expires_at,
        "jti": jti,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def create_access_token(subject: str, extra: dict | None = None) -> str:
    settings = get_settings()
    token, _, _ = _encode(
        subject,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        extra,
    )
    return token


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at) so the caller can persist the jti."""
    settings = get_settings()
    return _encode(
        subject, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str) -> dict:
    """Decode + verify a JWT. Raises ``jose.JWTError`` on any failure."""
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
