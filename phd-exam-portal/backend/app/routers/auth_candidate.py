"""Candidate authentication -- `/api/auth/candidate/*`.

Completely independent of the admin flow: its own cookie, its own JWT scope,
its own rate-limit bucket. Nothing here can mint or accept an admin token.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import client_ip, get_current_candidate
from app.core.redis_client import rate_limit_hit, reset_rate_limit
from app.core.security import cookie_kwargs, create_token, verify_password
from app.db.session import get_db
from app.enums import ActorType, CandidateStatus
from app.models import Candidate
from app.schemas import CandidateOut, LoginRequest, LoginResponse, MessageOut
from app.services.audit import log_action

router = APIRouter(prefix="/api/auth/candidate", tags=["auth:candidate"])

GENERIC_FAILURE = "Invalid email or password."


@router.post("/login", response_model=LoginResponse)
async def candidate_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    allowed, remaining = await rate_limit_hit(
        f"login:candidate:{ip}:{payload.email}",
        settings.LOGIN_RATE_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        await log_action(
            db,
            actor_type=ActorType.SYSTEM,
            action="candidate.login.rate_limited",
            actor_label=payload.email,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a few minutes.",
        )

    candidate = (
        await db.execute(select(Candidate).where(Candidate.email == payload.email))
    ).scalar_one_or_none()

    # Same message and roughly the same work whether the account exists or
    # not, so the endpoint does not leak which emails are registered.
    if candidate is None or not verify_password(payload.password, candidate.hashed_password):
        await log_action(
            db,
            actor_type=ActorType.CANDIDATE,
            action="candidate.login.failed",
            actor_id=candidate.id if candidate else None,
            actor_label=payload.email,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_FAILURE)

    if candidate.status in (CandidateStatus.BLOCKED, CandidateStatus.DISQUALIFIED):
        await log_action(
            db,
            actor_type=ActorType.CANDIDATE,
            action="candidate.login.blocked",
            actor_id=candidate.id,
            actor_label=candidate.email,
            ip_address=ip,
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your account is {candidate.status.value.lower()}. Contact the exam cell.",
        )
    if not candidate.can_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your registration has not been approved yet.",
        )

    token, expires = create_token(candidate.id, "candidate")
    response.set_cookie(
        settings.CANDIDATE_COOKIE_NAME,
        token,
        **cookie_kwargs(settings.CANDIDATE_TOKEN_MINUTES * 60),
    )
    # A successful login clears the throttle for this ip+identity pair.
    await reset_rate_limit(
        f"login:candidate:{ip}:{payload.email}", settings.LOGIN_RATE_WINDOW_SECONDS
    )

    candidate.last_login_at = datetime.now(timezone.utc)
    if candidate.status is CandidateStatus.APPROVED:
        candidate.status = CandidateStatus.ACTIVE
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="candidate.login.success",
        actor_id=candidate.id,
        actor_label=candidate.email,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return LoginResponse(redirect="/dashboard", expires_at=expires)


@router.post("/logout", response_model=MessageOut)
async def candidate_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    response.delete_cookie(settings.CANDIDATE_COOKIE_NAME, path="/")
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="candidate.logout",
        actor_id=candidate.id,
        actor_label=candidate.email,
        ip_address=client_ip(request),
        commit=True,
    )
    return MessageOut(message="Signed out.")


@router.get("/me", response_model=CandidateOut)
async def candidate_me(candidate: Candidate = Depends(get_current_candidate)):
    return candidate
