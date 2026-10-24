# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend import models, schemas, security
from backend.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    # Deliberately identical error for "no such user" and "wrong password" —
    # don't leak which one it was.
    if not user or not security.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = security.create_access_token(user)
    return schemas.LoginResponse(
        access_token=token,
        role=user.role.value,
        must_reset_password=user.must_reset_password,
        full_name=user.full_name,
    )


@router.post("/reset-password")
def reset_password(
    payload: schemas.ResetPasswordRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(security.get_current_user),
):
    """
    Self-service password reset. Available to setters and admin accounts.
    Candidates use the universal password (dsirt@123) and do not go through
    a reset flow; their passwords are managed by admin via the admin panel.
    """
    user.hashed_password = security.hash_password(payload.new_password)
    user.must_reset_password = False
    db.add(user)
    db.commit()
    token = security.create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role.value,
        "must_reset_password": False,
    }


@router.get("/me")
def me(user: models.User = Depends(security.get_current_user)):
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role.value,
        "must_reset_password": user.must_reset_password,
    }
