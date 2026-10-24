"""Admin authentication -- `/api/auth/admin/*`.

Mirror image of the candidate flow, sharing no dependency with it. A candidate
JWT presented here fails at the scope check; an admin JWT presented to a
candidate endpoint fails the same way.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import client_ip, get_current_admin
from app.core.redis_client import rate_limit_hit, reset_rate_limit
from app.core.security import cookie_kwargs, create_token, verify_password
from app.db.session import get_db
from app.enums import ActorType
from app.models import Admin
from app.schemas import AdminOut, LoginRequest, LoginResponse, MessageOut
from app.services.audit import log_action

router = APIRouter(prefix="/api/auth/admin", tags=["auth:admin"])

GENERIC_FAILURE = "Invalid email or password."


@router.post("/login", response_model=LoginResponse)
async def admin_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    allowed, _ = await rate_limit_hit(
        f"login:admin:{ip}:{payload.email}",
        settings.LOGIN_RATE_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        await log_action(
            db,
            actor_type=ActorType.SYSTEM,
            action="admin.login.rate_limited",
            actor_label=payload.email,
            ip_address=ip,
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a few minutes.",
        )

    admin = (
        await db.execute(select(Admin).where(Admin.email == payload.email))
    ).scalar_one_or_none()

    if admin is None or not admin.is_active or not verify_password(
        payload.password, admin.hashed_password
    ):
        await log_action(
            db,
            actor_type=ActorType.ADMIN,
            action="admin.login.failed",
            actor_id=admin.id if admin else None,
            actor_label=payload.email,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_FAILURE)

    token, expires = create_token(
        admin.id, "admin", extra_claims={"role": admin.role.value}
    )
    response.set_cookie(
        settings.ADMIN_COOKIE_NAME,
        token,
        **cookie_kwargs(settings.ADMIN_TOKEN_MINUTES * 60),
    )
    await reset_rate_limit(
        f"login:admin:{ip}:{payload.email}", settings.LOGIN_RATE_WINDOW_SECONDS
    )

    admin.last_login_at = datetime.now(timezone.utc)
    await log_action(
        db,
        actor_type=ActorType.ADMIN,
        action="admin.login.success",
        actor_id=admin.id,
        actor_label=admin.email,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return LoginResponse(redirect="/admin/dashboard", expires_at=expires)


@router.post("/logout", response_model=MessageOut)
async def admin_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    response.delete_cookie(settings.ADMIN_COOKIE_NAME, path="/")
    await log_action(
        db,
        actor_type=ActorType.ADMIN,
        action="admin.logout",
        actor_id=admin.id,
        actor_label=admin.email,
        ip_address=client_ip(request),
        commit=True,
    )
    return MessageOut(message="Signed out.")


@router.get("/me", response_model=AdminOut)
async def admin_me(admin: Admin = Depends(get_current_admin)):
    return admin
