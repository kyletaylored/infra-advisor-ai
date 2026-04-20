import os
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel

from database import get_user_by_id

JWT_SECRET: str = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


# ─── Pydantic output model ────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_service_account: bool
    created_at: str  # ISO format


# ─── Password helpers ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ─── JWT helpers ──────────────────────────────────────────────────────────────

def create_token(user: dict) -> str:
    """Issue a signed JWT for the given user dict (as returned by DB helpers)."""
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException 401 on any failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ─── FastAPI dependencies ─────────────────────────────────────────────────────

def require_auth(authorization: str = Header(default=None)) -> UserOut:
    """Dependency: validates Bearer token and returns the authenticated UserOut."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    payload = decode_token(token)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token payload missing subject")

    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return UserOut(
        id=user["id"],
        email=user["email"],
        is_admin=user["is_admin"],
        is_service_account=user["is_service_account"],
        created_at=user["created_at"],
    )


def require_admin(user: UserOut = Depends(require_auth)) -> UserOut:
    """Dependency: requires the authenticated user to have is_admin=True."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
