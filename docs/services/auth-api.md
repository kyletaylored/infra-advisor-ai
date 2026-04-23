---
title: Auth API
parent: Services
nav_order: 3
---

# Auth API

**Port:** 8002 | **Framework:** FastAPI + SQLAlchemy + PostgreSQL | **Replicas:** 2

The Auth API handles user registration, authentication, password reset, and admin-level user management. It issues JWT tokens that the browser includes on every subsequent API request.

## Endpoints

### Public endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Register a new user |
| `POST` | `/login` | Authenticate and receive a JWT |
| `POST` | `/forgot-password` | Request a password reset email |
| `POST` | `/reset-password` | Consume a reset token and set a new password |
| `GET` | `/health` | Service status |

### Authenticated endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/me` | Get current user profile |

### Admin-only endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/users` | List all users |
| `POST` | `/admin/users` | Create a user (bypasses domain restriction) |
| `DELETE` | `/admin/users/{user_id}` | Delete a user (cannot delete self) |
| `PATCH` | `/admin/users/{user_id}` | Toggle `is_admin` or `is_service_account` flag |

## Registration

By default, only `@datadoghq.com` email addresses can self-register (configurable via `ALLOWED_DOMAIN` env var). The first user to register becomes an admin automatically.

```bash
curl -X POST https://infra-advisor-ai.kyletaylor.dev/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@datadoghq.com", "password": "your-password"}'
```

Admin users can create accounts for any email domain via `POST /admin/users`.

## JWT authentication

Login returns a JWT token:

```bash
curl -X POST https://infra-advisor-ai.kyletaylor.dev/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@datadoghq.com", "password": "your-password"}'
```

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6...",
  "user": {
    "id": "550e8400-...",
    "email": "you@datadoghq.com",
    "is_admin": true
  }
}
```

Include this token on every API request:
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6...
```

JWT tokens expire after 24 hours. There is no refresh endpoint — users log in again after expiry.

## Password reset flow

The password reset flow uses email delivery via SMTP (MailHog captures email in dev/demo):

```
1. POST /forgot-password {"email": "you@datadoghq.com"}
   → Always returns 200 (prevents email enumeration)
   → If email exists: generates cryptographically secure token (secrets.token_urlsafe(32))
   → Stores SHA-256 hash in DB with 1-hour expiry
   → Sends email via SMTP with reset link: {APP_BASE_URL}/?reset_token={token}

2. User clicks link → browser navigates to /?reset_token=...
   → UI detects ?reset_token parameter on load
   → Switches to "reset password" mode

3. POST /reset-password {"token": "...", "new_password": "..."}
   → Validates token hash exists and not expired
   → Enforces minimum 8-character password
   → Updates password hash, clears reset token
   → Returns new JWT (auto-login on reset)
```

SMTP configuration:

| Env var | Default | Description |
|---------|---------|-------------|
| `SMTP_HOST` | (none) | SMTP server hostname. If unset, reset link is logged at INFO level |
| `SMTP_PORT` | 587 | SMTP port |
| `SMTP_USER` | | SMTP username |
| `SMTP_PASSWORD` | | SMTP password |
| `SMTP_FROM` | | Sender address |
| `SMTP_TLS` | `true` | Set to `false` for MailHog (no STARTTLS) |
| `APP_BASE_URL` | | Base URL for reset links (e.g., `https://infra-advisor-ai.kyletaylor.dev`) |

## Database schema

The `users` table in PostgreSQL:

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `email` | TEXT | Unique, lowercased |
| `password_hash` | TEXT | bcrypt hash |
| `is_admin` | BOOLEAN | Default false |
| `is_service_account` | BOOLEAN | Default false (bypasses domain restriction) |
| `created_at` | TIMESTAMPTZ | Auto-set |
| `reset_token_hash` | TEXT | SHA-256 of current reset token, nullable |
| `reset_token_expires` | TIMESTAMPTZ | Token expiry (1 hour from creation), nullable |

The schema is created on startup via `init_db()` which uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — safe for both fresh installs and upgrades.

## Observability

**APM:** All HTTP requests traced via `ddtrace.auto`. SQL queries appear as child spans of each HTTP span.

**DBM (Database Monitoring):** `DD_DBM_PROPAGATION_MODE=full` is set in the auth-api configmap. This injects full trace context into SQL comments, allowing Datadog DBM to correlate slow query samples and `EXPLAIN` plans back to the originating APM trace.

The Datadog monitoring role has read-only access to `pg_stat_statements` for query analytics.

**Log annotation:**
```yaml
ad.datadoghq.com/auth-api.logs: '[{"source": "auth-api", "service": "auth-api"}]'
```

## MailHog (dev/demo SMTP capture)

In the dev/demo environment, MailHog intercepts all outbound email. No real email is delivered.

- **SMTP:** `mailhog.infra-advisor.svc.cluster.local:1025` (no TLS)
- **Web UI:** `https://infra-advisor-ai.kyletaylor.dev/mailhog`
- **Storage:** In-memory (email visible only until pod restart)

MailHog is configured via `k8s/auth-api/configmap.yaml`:
```yaml
SMTP_HOST: mailhog.infra-advisor.svc.cluster.local
SMTP_PORT: "1025"
SMTP_TLS: "false"
SMTP_FROM: infra-advisor-ai@demo.local
```
