"""JWT validation + per-user rate limiting for agent-api.

Mirrors auth-api's HS256 token format using the same `JWT_SECRET` env var
so a token issued by auth-api's /login endpoint validates here without a
round-trip. See services/auth-api/src/auth.py for the issuing side.

The rate limiter is a singleton `Limiter` from slowapi, keyed by user
ID when authenticated, falling back to the client IP. Heavy LLM endpoints
get per-user limits (the legitimate cost ceiling); /health and the
bootstrap endpoints get IP-only limits or no limits at all.
"""

import os
from typing import Any

from fastapi import Header, HTTPException, Request
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

JWT_SECRET = os.environ.get("JWT_SECRET")
ALGORITHM = "HS256"


# ─── JWT validation ───────────────────────────────────────────────────────────

def decode_token(token: str) -> dict[str, Any]:
    if not JWT_SECRET:
        # Fail closed: if the deployment is missing the shared secret, every
        # request is rejected — never silently treat as anonymous.
        raise HTTPException(
            status_code=503,
            detail="Server misconfigured: JWT_SECRET not set",
        )
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_auth(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """FastAPI dependency: validates `Authorization: Bearer <jwt>` and returns
    the decoded claims. Add as `user=Depends(require_auth)` on any handler
    that should require login.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    payload = decode_token(token)
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Token payload missing subject")
    return payload


# ─── Rate limiting ────────────────────────────────────────────────────────────

def _rate_key(request: Request) -> str:
    """Key the limiter by user ID when the request is authenticated (so a user
    can't multiply their quota by switching IPs), and fall back to client IP
    for endpoints we haven't fully locked yet."""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = jwt.decode(
                token,
                JWT_SECRET or "",
                algorithms=[ALGORITHM],
                options={"verify_signature": bool(JWT_SECRET)},
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return f"ip:{get_remote_address(request)}"


# Per-route limits applied via `@limiter.limit(...)` on the handler.
# Defaults chosen for a research/demo app, not high-traffic prod.
limiter = Limiter(
    key_func=_rate_key,
    default_limits=["120/minute"],   # safety net across everything
    headers_enabled=False,           # would require Response= param on every handler
)
