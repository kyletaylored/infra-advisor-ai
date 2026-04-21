"""Tests for password reset flow — POST /forgot-password and POST /reset-password."""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

# Patch DB engine creation before importing app modules
with patch("sqlalchemy.create_engine"), patch("database.init_db"):
    from fastapi.testclient import TestClient
    import main as app_module
    from main import app

client = TestClient(app, raise_server_exceptions=False)


def _make_user(
    user_id: str = "user-1",
    email: str = "test@datadoghq.com",
    reset_token_hash: str | None = None,
    reset_token_expires: datetime | None = None,
) -> dict:
    return {
        "id": user_id,
        "email": email,
        "password_hash": "$2b$12$placeholder",
        "is_admin": False,
        "is_service_account": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "reset_token_hash": reset_token_hash,
        "reset_token_expires": reset_token_expires,
    }


# ── POST /forgot-password ─────────────────────────────────────────────────────

class TestForgotPassword:
    def test_existing_user_generates_token_and_returns_200(self):
        user = _make_user()
        with (
            patch("main.get_user_by_email", return_value=user),
            patch("main.set_reset_token") as mock_set,
            patch("main._send_reset_email") as mock_send,
        ):
            resp = client.post("/forgot-password", json={"email": "test@datadoghq.com"})

        assert resp.status_code == 200
        assert "reset link" in resp.json()["message"].lower()
        mock_set.assert_called_once()
        mock_send.assert_called_once()

    def test_unknown_email_still_returns_200(self):
        """Must not leak whether email is registered."""
        with (
            patch("main.get_user_by_email", return_value=None),
            patch("main.set_reset_token") as mock_set,
            patch("main._send_reset_email") as mock_send,
        ):
            resp = client.post("/forgot-password", json={"email": "nobody@datadoghq.com"})

        assert resp.status_code == 200
        mock_set.assert_not_called()
        mock_send.assert_not_called()

    def test_email_is_normalised_to_lowercase(self):
        user = _make_user(email="test@datadoghq.com")
        with (
            patch("main.get_user_by_email", return_value=user) as mock_get,
            patch("main.set_reset_token"),
            patch("main._send_reset_email"),
        ):
            client.post("/forgot-password", json={"email": "TEST@Datadoghq.COM"})

        mock_get.assert_called_once_with("test@datadoghq.com")


# ── POST /reset-password ──────────────────────────────────────────────────────

class TestResetPassword:
    def _valid_token_setup(self, token: str = "validtoken123"):
        from auth import hash_reset_token
        token_hash = hash_reset_token(token)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        user = _make_user(reset_token_hash=token_hash, reset_token_expires=expires)
        return user, token

    def test_valid_token_resets_password_and_returns_token(self):
        user, token = self._valid_token_setup()
        updated_user = _make_user()

        with (
            patch("main.get_user_by_reset_token", return_value=user),
            patch("main.update_user", return_value=updated_user),
            patch("main.clear_reset_token") as mock_clear,
        ):
            resp = client.post("/reset-password", json={
                "token": token,
                "new_password": "newpassword123",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert body["user"]["email"] == user["email"]
        mock_clear.assert_called_once_with(user["id"])

    def test_invalid_token_returns_400(self):
        with patch("main.get_user_by_reset_token", return_value=None):
            resp = client.post("/reset-password", json={
                "token": "bogustoken",
                "new_password": "newpassword123",
            })

        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_expired_token_returns_400(self):
        from auth import hash_reset_token
        token = "expiredtoken"
        token_hash = hash_reset_token(token)
        expired_time = datetime.now(timezone.utc) - timedelta(hours=2)
        user = _make_user(reset_token_hash=token_hash, reset_token_expires=expired_time)

        with (
            patch("main.get_user_by_reset_token", return_value=user),
            patch("main.clear_reset_token"),
        ):
            resp = client.post("/reset-password", json={
                "token": token,
                "new_password": "newpassword123",
            })

        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    def test_short_password_returns_400(self):
        user, token = self._valid_token_setup()

        with patch("main.get_user_by_reset_token", return_value=user):
            resp = client.post("/reset-password", json={
                "token": token,
                "new_password": "short",
            })

        assert resp.status_code == 400
        assert "8 characters" in resp.json()["detail"]

    def test_token_is_hashed_before_lookup(self):
        """Ensure raw token is never stored — only the SHA-256 hash is looked up."""
        from auth import hash_reset_token
        raw_token = "plaintexttoken"
        expected_hash = hash_reset_token(raw_token)

        with patch("main.get_user_by_reset_token", return_value=None) as mock_lookup:
            client.post("/reset-password", json={
                "token": raw_token,
                "new_password": "newpassword123",
            })

        mock_lookup.assert_called_once_with(expected_hash)


# ── Auth helpers ──────────────────────────────────────────────────────────────

class TestAuthHelpers:
    def test_generate_reset_token_is_url_safe_and_unique(self):
        from auth import generate_reset_token
        t1, t2 = generate_reset_token(), generate_reset_token()
        assert t1 != t2
        assert len(t1) > 20
        # URL-safe characters only
        import urllib.parse
        assert urllib.parse.quote(t1, safe="-_") == t1

    def test_hash_reset_token_is_deterministic(self):
        from auth import hash_reset_token
        t = "sometoken"
        assert hash_reset_token(t) == hash_reset_token(t)

    def test_hash_reset_token_differs_from_plaintext(self):
        from auth import hash_reset_token
        t = "sometoken"
        assert hash_reset_token(t) != t
