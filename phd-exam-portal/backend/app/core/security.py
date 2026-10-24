"""Password hashing and JWT minting/verification.

Candidate and admin tokens are minted with a distinct ``scope`` claim and are
carried in distinct cookies. A token issued for one audience is rejected
outright by the other's guard -- there is no shared "current user".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

Scope = Literal["candidate", "admin"]

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


# ------------------------------------------------------------------ passwords
def hash_password(raw: str) -> str:
    # bcrypt silently truncates beyond 72 bytes; reject rather than surprise.
    if len(raw.encode("utf-8")) > 72:
        raise ValueError("Password exceeds bcrypt's 72-byte limit")
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except ValueError:
        return False


# ---------------------------------------------------------------------- jwt
def create_token(
    subject: uuid.UUID | str,
    scope: Scope,
    *,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    if expires_minutes is None:
        expires_minutes = (
            settings.ADMIN_TOKEN_MINUTES
            if scope == "admin"
            else settings.CANDIDATE_TOKEN_MINUTES
        )
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "scope": scope,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire


def decode_token(token: str, expected_scope: Scope) -> dict[str, Any] | None:
    """Return the claims, or ``None`` if invalid/expired/wrong audience."""
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    if claims.get("scope") != expected_scope:
        return None
    if not claims.get("sub"):
        return None
    return claims


def cookie_kwargs(max_age_seconds: int) -> dict[str, Any]:
    """Shared hardening flags for both auth cookies."""
    kwargs: dict[str, Any] = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "max_age": max_age_seconds,
        "path": "/",
    }
    if settings.COOKIE_DOMAIN:
        kwargs["domain"] = settings.COOKIE_DOMAIN
    return kwargs
