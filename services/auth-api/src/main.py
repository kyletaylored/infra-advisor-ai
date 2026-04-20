import ddtrace.auto  # must be first import — monkey-patches sqlalchemy, fastapi at import time

import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import UserOut, create_token, hash_password, require_admin, require_auth, verify_password
from database import (
    count_users,
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
    init_db,
    list_users,
    update_user,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = "@datadoghq.com"

app = FastAPI(title="InfraAdvisor Auth API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Initialising database schema...")
    init_db()
    logger.info("auth-api ready")


# ─── Request / response models ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: UserOut


class AdminCreateUserRequest(BaseModel):
    email: str
    password: str
    is_admin: bool = False
    is_service_account: bool = False


class AdminPatchUserRequest(BaseModel):
    is_admin: bool | None = None
    is_service_account: bool | None = None


# ─── Helper ───────────────────────────────────────────────────────────────────

def _user_dict_to_out(u: dict) -> UserOut:
    return UserOut(
        id=u["id"],
        email=u["email"],
        is_admin=u["is_admin"],
        is_service_account=u["is_service_account"],
        created_at=u["created_at"],
    )


# ─── Public endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest):
    email = body.email.strip().lower()

    if not email.endswith(ALLOWED_DOMAIN):
        raise HTTPException(
            status_code=400,
            detail=f"Registration is restricted to {ALLOWED_DOMAIN} email addresses",
        )

    if get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Email already registered")

    # First user ever gets admin automatically
    first_user = count_users() == 0
    password_hash = hash_password(body.password)
    user = create_user(
        email=email,
        password_hash=password_hash,
        is_admin=first_user,
        is_service_account=False,
    )

    token = create_token(user)
    return TokenResponse(token=token, user=_user_dict_to_out(user))


@app.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    email = body.email.strip().lower()
    user = get_user_by_email(email)

    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user)
    return TokenResponse(token=token, user=_user_dict_to_out(user))


@app.get("/me", response_model=UserOut)
def me(current_user: UserOut = Depends(require_auth)):
    return current_user


# ─── Admin endpoints ──────────────────────────────────────────────────────────

@app.get("/admin/users", response_model=list[UserOut])
def admin_list_users(admin: UserOut = Depends(require_admin)):
    return [_user_dict_to_out(u) for u in list_users()]


@app.post("/admin/users", response_model=UserOut, status_code=201)
def admin_create_user(body: AdminCreateUserRequest, admin: UserOut = Depends(require_admin)):
    email = body.email.strip().lower()

    # Service accounts bypass domain restriction; regular users must match
    if not body.is_service_account and not email.endswith(ALLOWED_DOMAIN):
        raise HTTPException(
            status_code=400,
            detail=f"Non-service-account users must have {ALLOWED_DOMAIN} email addresses",
        )

    if get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Email already registered")

    password_hash = hash_password(body.password)
    user = create_user(
        email=email,
        password_hash=password_hash,
        is_admin=body.is_admin,
        is_service_account=body.is_service_account,
    )
    return _user_dict_to_out(user)


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: str, admin: UserOut = Depends(require_admin)):
    if user_id == admin.id:
        raise HTTPException(status_code=403, detail="You cannot delete your own account")
    if not delete_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return {"deleted": True}


@app.patch("/admin/users/{user_id}", response_model=UserOut)
def admin_patch_user(
    user_id: str,
    body: AdminPatchUserRequest,
    admin: UserOut = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=403, detail="You cannot modify your own account")

    if get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    fields = {}
    if body.is_admin is not None:
        fields["is_admin"] = body.is_admin
    if body.is_service_account is not None:
        fields["is_service_account"] = body.is_service_account

    updated = update_user(user_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_dict_to_out(updated)
