"""
Admin-only account management for the platform's two non-admin roles:

  * setter    — question updater, manages the question bank
  * candidate — exam attender, sits the exam

Admin accounts are not created or edited through this router — there is
intentionally no self-service or API path to mint new admins.

There is no "forgot password" email/self-service flow in this platform.
Candidates use the universal password dsirt@123 issued by the institution.
If a setter forgets their password, the admin resets it here and passes
the new temporary password on to them directly.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend import models, schemas, security
from backend.config import CANDIDATE_DEFAULT_PASSWORD
from backend.database import get_db
from backend.security import get_current_admin

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

MANAGEABLE_ROLES = (models.UserRole.setter, models.UserRole.candidate)


def _latest_session(db: Session, user: models.User) -> Optional[models.ExamSession]:
    if user.role != models.UserRole.candidate:
        return None
    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user.id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    return session


def _to_out(db: Session, user: models.User) -> schemas.ManagedUserOut:
    session = _latest_session(db, user)
    return schemas.ManagedUserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
        must_reset_password=user.must_reset_password,
        latest_session_status=session.status.value if session else None,
        latest_session_score=session.score if session else None,
    )


def _get_managed_user_or_404(db: Session, user_id: int) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role not in MANAGEABLE_ROLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=List[schemas.ManagedUserOut])
def list_managed_users(
    role: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    query = db.query(models.User).filter(models.User.role.in_(MANAGEABLE_ROLES))
    if role:
        if role not in ("setter", "candidate"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='role must be "setter" or "candidate"')
        query = query.filter(models.User.role == role)
    users = query.order_by(models.User.role, models.User.full_name).all()
    return [_to_out(db, u) for u in users]


@router.post("", response_model=schemas.CreateManagedUserResponse, status_code=status.HTTP_201_CREATED)
def create_managed_user(
    payload: schemas.CreateManagedUserRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists")

    if payload.role == "candidate":
        # Candidates use the universal password — no temp password, no forced reset.
        password = CANDIDATE_DEFAULT_PASSWORD
        must_reset = False
    else:
        # Setters get a random temp password that they must change on first login.
        password = security.generate_temp_password()
        must_reset = True

    user = models.User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=security.hash_password(password),
        role=models.UserRole(payload.role),
        approval_status="approved",
        must_reset_password=must_reset,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return schemas.CreateManagedUserResponse(user=_to_out(db, user), temporary_password=password)


@router.patch("/{user_id}", response_model=schemas.ManagedUserOut)
def update_managed_user(
    user_id: int,
    payload: schemas.UpdateManagedUserRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    user = _get_managed_user_or_404(db, user_id)

    if payload.email is not None and payload.email != user.email:
        clash = db.query(models.User).filter(models.User.email == payload.email, models.User.id != user.id).first()
        if clash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists")
        user.email = payload.email

    if payload.full_name is not None:
        user.full_name = payload.full_name

    if payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            # Deactivating also revokes any token they're currently holding.
            user.token_version = (user.token_version or 0) + 1

    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_out(db, user)


@router.post("/{user_id}/reset-password", response_model=schemas.AdminResetPasswordResponse)
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Admin-driven password reset.
    For candidates: resets to the universal password dsirt@123.
    For setters: generates a new temporary random password (they must reset on next login).
    """
    user = _get_managed_user_or_404(db, user_id)

    if user.role == models.UserRole.candidate:
        # Candidates always reset to the universal password
        new_password = CANDIDATE_DEFAULT_PASSWORD
        user.must_reset_password = False
    else:
        new_password = security.generate_temp_password()
        user.must_reset_password = True

    user.hashed_password = security.hash_password(new_password)
    # Invalidate any token they're currently holding.
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()
    return schemas.AdminResetPasswordResponse(
        user_id=user.id, email=user.email, temporary_password=new_password
    )


@router.post("/{user_id}/reactivate", response_model=schemas.ReactivateCandidateResponse)
def reactivate_candidate(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Clears a candidate's entire exam history so they can take the exam from scratch.
    Use grant-retake instead if you want to preserve their saved answers."""
    user = _get_managed_user_or_404(db, user_id)
    if user.role != models.UserRole.candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only candidates have exam attempts to reactivate")

    sessions = db.query(models.ExamSession).filter(models.ExamSession.user_id == user.id).all()
    for s in sessions:
        db.delete(s)  # cascades to answers/violations

    user.is_active = True
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()

    return schemas.ReactivateCandidateResponse(
        user_id=user.id,
        email=user.email,
        message="Previous attempt cleared. Candidate can sign in and start the exam fresh.",
    )
