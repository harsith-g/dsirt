import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
import bcrypt
from sqlalchemy.orm import Session

from backend.config import SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from backend.database import get_db, SessionLocal
from backend import models

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('ascii')


def generate_temp_password() -> str:
    """Generates a one-time temporary password for accounts the admin
    creates or resets. Shown once in the admin UI — the admin is
    responsible for passing it on to the account holder out of band."""
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{body}!"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('ascii'))
    except ValueError:
        return False


def create_access_token(user: models.User) -> str:
    """
    The JWT itself carries role + must_reset_password so the frontend can
    branch (login -> reset vs. login -> exam) without an extra round trip.
    None of these claims are trusted for authorization decisions server-side
    beyond `sub` (user id) — every protected endpoint re-reads the user's
    current row from the database rather than trusting stale token claims.
    """
    now = datetime.utcnow()
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "must_reset_password": user.must_reset_password,
        "tv": user.token_version,
        "iat": int(now.timestamp()),
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> models.User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(token)
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.id == int(payload["sub"])).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
        if payload.get("tv", -1) < user.token_version:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
        db.expunge(user)
        return user
    finally:
        db.close()


def get_current_candidate(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.candidate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Candidate access only")
    if user.must_reset_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password reset required before exam access")
    return user


def get_current_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access only")
    return user


def get_current_setter(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.setter:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Question setter access only")
    return user


def get_current_staff(user: models.User = Depends(get_current_user)) -> models.User:
    """Admin or question setter — used for endpoints (the question bank)
    that both roles are allowed to manage."""
    if user.role not in (models.UserRole.admin, models.UserRole.setter):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access only")
    return user


def decode_token_for_ws(token: str, db: Session) -> models.User:
    """Same as get_current_admin but usable outside FastAPI's Depends graph,
    for the WebSocket handshake where headers/query params substitute for
    the normal Authorization header."""
    payload = decode_token(token)
    user = db.query(models.User).filter(models.User.id == int(payload["sub"])).first()
    if not user or user.role != models.UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access only")
    return user
