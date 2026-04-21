import ddtrace.auto  # must be first import — monkey-patches sqlalchemy, fastapi at import time

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import (
    UserOut,
    create_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    require_admin,
    require_auth,
    reset_token_expiry,
    verify_password,
)
from database import (
    clear_reset_token,
    count_users,
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_reset_token,
    init_db,
    list_users,
    set_reset_token,
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


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


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


# ─── Email helper ─────────────────────────────────────────────────────────────

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5173")


def _send_reset_email(to_email: str, token: str) -> None:
    """Send a password reset email via SMTP, or log the link if SMTP is not configured."""
    reset_url = f"{APP_BASE_URL}?reset_token={token}"

    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        # No SMTP configured — log the link so devs can use it directly
        logger.info(
            "PASSWORD RESET LINK (no SMTP configured) — %s — %s",
            to_email,
            reset_url,
        )
        return

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    body = (
        f"Hi,\n\n"
        f"You requested a password reset for your InfraAdvisor AI account.\n\n"
        f"Click the link below to set a new password (expires in 1 hour):\n\n"
        f"  {reset_url}\n\n"
        f"If you did not request this, you can safely ignore this email.\n\n"
        f"— InfraAdvisor AI"
    )
    msg = MIMEText(body)
    msg["Subject"] = "InfraAdvisor AI — password reset"
    msg["From"] = smtp_from
    msg["To"] = to_email

    use_tls = os.environ.get("SMTP_TLS", "true").lower() != "false"

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        logger.info("Password reset email sent to %s", to_email)
    except Exception as exc:
        logger.warning("Failed to send reset email to %s: %s", to_email, exc)
        logger.info("PASSWORD RESET LINK (SMTP failed) — %s — %s", to_email, reset_url)


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


@app.post("/forgot-password", status_code=200)
def forgot_password(body: ForgotPasswordRequest):
    """Request a password reset link. Always returns 200 to avoid leaking user existence."""
    email = body.email.strip().lower()
    user = get_user_by_email(email)

    if user:
        token = generate_reset_token()
        token_hash = hash_reset_token(token)
        expires = reset_token_expiry()
        set_reset_token(user["id"], token_hash, expires)
        _send_reset_email(email, token)

    return {"message": "If that email is registered, a reset link has been sent."}


@app.post("/reset-password", response_model=TokenResponse)
def reset_password(body: ResetPasswordRequest):
    """Consume a reset token and set a new password."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_hash = hash_reset_token(body.token)
    user = get_user_by_reset_token(token_hash)

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Check expiry
    expires = user.get("reset_token_expires")
    if expires:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            clear_reset_token(user["id"])
            raise HTTPException(status_code=400, detail="Reset token has expired")

    new_hash = hash_password(body.new_password)
    updated = update_user(user["id"], password_hash=new_hash)
    clear_reset_token(user["id"])

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update password")

    token = create_token(updated)
    return TokenResponse(token=token, user=_user_dict_to_out(updated))


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
