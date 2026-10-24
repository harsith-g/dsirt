"""Role-based access control dependencies.

There is deliberately **no** shared `get_current_user`. A candidate guard only
ever reads the candidate cookie, and an admin guard only ever reads the admin
cookie. Presenting a candidate JWT to an admin endpoint fails at the scope
check inside `decode_token` before any database lookup happens.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import rate_limit_hit
from app.core.security import decode_token
from app.db.session import get_db
from app.models import Admin, AdminRole, Candidate, CandidateStatus


class NotAuthenticatedError(HTTPException):
    """Raised for API calls; page routes catch this and redirect instead."""

    def __init__(self, scope: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Not authenticated as {scope}",
            headers={"WWW-Authenticate": "Cookie"},
        )
        self.scope = scope


def client_ip(request: Request) -> str:
    """Trust the left-most X-Forwarded-For entry set by our own Nginx."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


# ----------------------------------------------------------------- candidate
async def get_current_candidate(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Candidate:
    token = request.cookies.get(settings.CANDIDATE_COOKIE_NAME)
    if not token:
        raise NotAuthenticatedError("candidate")
    claims = decode_token(token, "candidate")
    if not claims:
        raise NotAuthenticatedError("candidate")
    try:
        candidate_id = uuid.UUID(claims["sub"])
    except (ValueError, KeyError):
        raise NotAuthenticatedError("candidate") from None

    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise NotAuthenticatedError("candidate")
    if candidate.status in (CandidateStatus.BLOCKED, CandidateStatus.DISQUALIFIED):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your account is {candidate.status.value.lower()}. Contact the exam cell.",
        )
    if not candidate.can_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your registration has not been approved yet.",
        )
    return candidate


# --------------------------------------------------------------------- admin
async def get_current_admin(request: Request, db: AsyncSession = Depends(get_db)) -> Admin:
    token = request.cookies.get(settings.ADMIN_COOKIE_NAME)
    if not token:
        raise NotAuthenticatedError("admin")
    claims = decode_token(token, "admin")
    if not claims:
        raise NotAuthenticatedError("admin")
    try:
        admin_id = uuid.UUID(claims["sub"])
    except (ValueError, KeyError):
        raise NotAuthenticatedError("admin") from None

    admin = await db.get(Admin, admin_id)
    if admin is None or not admin.is_active:
        raise NotAuthenticatedError("admin")
    return admin


def require_admin_role(*roles: AdminRole):
    """Guard factory for endpoints restricted to particular admin roles."""

    async def _guard(admin: Admin = Depends(get_current_admin)) -> Admin:
        if roles and admin.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges for this operation.",
            )
        return admin

    return _guard


require_super_admin = require_admin_role(AdminRole.SUPER_ADMIN)
require_admin = require_admin_role(AdminRole.SUPER_ADMIN, AdminRole.ADMIN)
require_proctor = require_admin_role(
    AdminRole.SUPER_ADMIN, AdminRole.ADMIN, AdminRole.PROCTOR
)


# --------------------------------------------------------------- rate limits
async def api_rate_limiter(request: Request) -> None:
    allowed, _ = await rate_limit_hit(
        f"api:{client_ip(request)}", settings.API_RATE_LIMIT, settings.API_RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Slow down.",
        )


# ------------------------------------------------- optional (for page routes)
async def optional_candidate(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Candidate | None:
    try:
        return await get_current_candidate(request, db)
    except HTTPException:
        return None


async def optional_admin(request: Request, db: AsyncSession = Depends(get_db)) -> Admin | None:
    try:
        return await get_current_admin(request, db)
    except HTTPException:
        return None
