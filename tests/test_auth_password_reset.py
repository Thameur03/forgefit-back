"""
tests/test_auth_password_reset.py

FastAPI integration tests for the password-reset endpoints.
Email delivery (Resend/SMTP) is mocked — no real email is sent.

SQLite compatibility note:
  main.py imports models.analytics_event (which has a JSONB column) and calls
  Base.metadata.create_all() at module-body level. SQLite cannot compile JSONB.
  We patch metadata.create_all to a no-op before importing main, then call it
  manually with only the SQLite-compatible tables we need.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone

# ── 1. Force test environment FIRST ──────────────────────────────────────────
TEST_DB = "sqlite:///./test_password_reset.db"
os.environ["DATABASE_URL"] = TEST_DB
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "false"
os.environ["RESEND_API_KEY"] = ""

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── 2. Set up the SQLite test engine BEFORE main is imported ─────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import database

test_engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Point the database module at the test engine so get_db uses our session.
database.engine = test_engine

# ── 3. Import main with create_all patched out ────────────────────────────────
# main.py runs Base.metadata.create_all(bind=engine) at module level.
# We intercept that call so the JSONB analytics_events table is never compiled
# against SQLite.
with patch.object(database.Base.metadata, "create_all"):
    from main import app

from database import get_db
from auth.utils import hash_password, verify_password
from models.user import User
from limiter import limiter

# ── 4. Now create only the SQLite-safe tables ─────────────────────────────────
# Remove analytics_events (JSONB) from metadata before create_all.
if "analytics_events" in database.Base.metadata.tables:
    database.Base.metadata.remove(database.Base.metadata.tables["analytics_events"])

database.Base.metadata.create_all(bind=test_engine)

# ── 5. Override the get_db dependency ─────────────────────────────────────────
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

# ── 6. TestClient after all setup is done ─────────────────────────────────────
from fastapi.testclient import TestClient
client = TestClient(app, raise_server_exceptions=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _db():
    return TestingSessionLocal()

def _create_user(email: str, password: str = "Password1"):
    db = _db()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name="Test User",
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()

def _get_user(email: str):
    db = _db()
    user = db.query(User).filter(User.email == email).first()
    db.close()
    return user

def _inject_otp(email: str, otp: str, expired: bool = False):
    db = _db()
    user = db.query(User).filter(User.email == email).first()
    delta = timedelta(hours=-1) if expired else timedelta(minutes=15)
    user.reset_password_code = hash_password(otp)
    user.reset_password_code_expires = datetime.now(timezone.utc) + delta
    db.commit()
    db.close()

# Patch email + rate limiter globally for all tests.
@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    """Disable email delivery and rate limiting for all tests."""
    with patch("auth.email._send_via_resend", return_value=False), \
         patch("auth.email._send_via_smtp", return_value=False):
        # Disable rate limiting: patch the name-mangled internal method that
        # slowapi calls for every decorated endpoint.
        monkeypatch.setattr(limiter, "_Limiter__evaluate_limits", lambda *a, **kw: None)
        yield


@pytest.fixture(autouse=True)
def clean_users():
    """Reset the users table before each test to prevent state leakage."""
    db = _db()
    db.query(User).delete()
    db.commit()
    db.close()
    yield


# ── Tests: forgot-password ─────────────────────────────────────────────────────

class TestForgotPassword:

    def test_existing_email_returns_200(self):
        _create_user("fp-exists@example.com")
        r = client.post("/auth/forgot-password", json={"email": "fp-exists@example.com"})
        assert r.status_code == 200
        assert "reset" in r.json()["message"].lower()

    def test_nonexistent_email_returns_same_200(self):
        r_missing = client.post("/auth/forgot-password", json={"email": "no-account@example.com"})
        r_exists  = client.post("/auth/forgot-password", json={"email": "fp-exists@example.com"})
        assert r_missing.status_code == 200
        assert r_missing.json() == r_exists.json()

    def test_stores_hashed_code_and_future_expiry(self):
        _create_user("fp-store@example.com")
        client.post("/auth/forgot-password", json={"email": "fp-store@example.com"})
        user = _get_user("fp-store@example.com")
        assert user.reset_password_code is not None
        assert user.reset_password_code_expires is not None
        exp = user.reset_password_code_expires
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        assert exp > datetime.now(timezone.utc)

    def test_second_request_overwrites_first_code(self):
        _create_user("fp-overwrite@example.com")
        client.post("/auth/forgot-password", json={"email": "fp-overwrite@example.com"})
        first = _get_user("fp-overwrite@example.com").reset_password_code
        client.post("/auth/forgot-password", json={"email": "fp-overwrite@example.com"})
        second = _get_user("fp-overwrite@example.com").reset_password_code
        assert first != second


# ── Tests: reset-password ──────────────────────────────────────────────────────

class TestResetPassword:

    def test_valid_otp_resets_password(self):
        _create_user("rp-valid@example.com")
        _inject_otp("rp-valid@example.com", "654321")
        r = client.post("/auth/reset-password",
                        json={"email": "rp-valid@example.com", "code": "654321", "new_password": "NewPass1"})
        assert r.status_code == 200

    def test_old_password_fails_after_reset(self):
        _create_user("rp-old@example.com")
        _inject_otp("rp-old@example.com", "111222")
        client.post("/auth/reset-password",
                    json={"email": "rp-old@example.com", "code": "111222", "new_password": "NewPass1"})
        r = client.post("/auth/login", json={"email": "rp-old@example.com", "password": "Password1"})
        assert r.status_code in (401, 403)

    def test_new_password_succeeds_after_reset(self):
        _create_user("rp-new@example.com")
        _inject_otp("rp-new@example.com", "333444")
        client.post("/auth/reset-password",
                    json={"email": "rp-new@example.com", "code": "333444", "new_password": "NewPass1"})
        r = client.post("/auth/login", json={"email": "rp-new@example.com", "password": "NewPass1"})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_invalid_otp_returns_400(self):
        _create_user("rp-invalid@example.com")
        _inject_otp("rp-invalid@example.com", "999888")
        r = client.post("/auth/reset-password",
                        json={"email": "rp-invalid@example.com", "code": "000000", "new_password": "NewPass1"})
        assert r.status_code == 400

    def test_expired_otp_returns_400(self):
        _create_user("rp-expired@example.com")
        _inject_otp("rp-expired@example.com", "555666", expired=True)
        r = client.post("/auth/reset-password",
                        json={"email": "rp-expired@example.com", "code": "555666", "new_password": "NewPass1"})
        assert r.status_code == 400

    def test_leading_zero_otp_preserved(self):
        _create_user("rp-leading@example.com")
        _inject_otp("rp-leading@example.com", "012345")
        r = client.post("/auth/reset-password",
                        json={"email": "rp-leading@example.com", "code": "012345", "new_password": "NewPass1"})
        assert r.status_code == 200

    def test_otp_reuse_fails(self):
        _create_user("rp-reuse@example.com")
        _inject_otp("rp-reuse@example.com", "777888")
        client.post("/auth/reset-password",
                    json={"email": "rp-reuse@example.com", "code": "777888", "new_password": "NewPass1"})
        r = client.post("/auth/reset-password",
                        json={"email": "rp-reuse@example.com", "code": "777888", "new_password": "AnotherPass1"})
        assert r.status_code == 400

    def test_weak_password_returns_422(self):
        _create_user("rp-weak@example.com")
        _inject_otp("rp-weak@example.com", "246810")
        r = client.post("/auth/reset-password",
                        json={"email": "rp-weak@example.com", "code": "246810", "new_password": "weak"})
        assert r.status_code == 422

    def test_nonexistent_user_returns_400(self):
        r = client.post("/auth/reset-password",
                        json={"email": "ghost@example.com", "code": "000000", "new_password": "Password1"})
        assert r.status_code == 400

    def test_reset_clears_code_fields(self):
        _create_user("rp-clear@example.com")
        _inject_otp("rp-clear@example.com", "135790")
        client.post("/auth/reset-password",
                    json={"email": "rp-clear@example.com", "code": "135790", "new_password": "NewPass1"})
        user = _get_user("rp-clear@example.com")
        assert user.reset_password_code is None
        assert user.reset_password_code_expires is None

    def test_second_code_wins_first_rejected(self):
        """Only the most recently issued OTP should be valid."""
        _create_user("rp-twocode@example.com")
        _inject_otp("rp-twocode@example.com", "aaaaaa".replace("a", "1"))  # "111111"
        # overwrite with a second OTP
        _inject_otp("rp-twocode@example.com", "222222")
        # first OTP must fail
        r1 = client.post("/auth/reset-password",
                         json={"email": "rp-twocode@example.com", "code": "111111", "new_password": "NewPass1"})
        assert r1.status_code == 400
        # second OTP must succeed
        r2 = client.post("/auth/reset-password",
                         json={"email": "rp-twocode@example.com", "code": "222222", "new_password": "NewPass1"})
        assert r2.status_code == 200


# ── Tests: email test-mode gate ───────────────────────────────────────────────

class TestEmailTestModeGate:

    def test_non_test_recipient_is_skipped(self):
        import auth.email as em
        saved_key, saved_rec = em.RESEND_API_KEY, em.RESEND_TEST_RECIPIENT
        try:
            em.RESEND_API_KEY = "fake_key"
            em.RESEND_TEST_RECIPIENT = "developer@acme.com"
            with patch.object(em, "_domain_verified", return_value=False), \
                 patch.object(em, "_send_via_resend") as mock_r:
                result = em._send_email("other@example.com", "S", "B")
                mock_r.assert_not_called()
                assert result is False
        finally:
            em.RESEND_API_KEY = saved_key
            em.RESEND_TEST_RECIPIENT = saved_rec

    def test_test_recipient_exact_match_delivers(self):
        import auth.email as em
        saved_key, saved_rec = em.RESEND_API_KEY, em.RESEND_TEST_RECIPIENT
        try:
            em.RESEND_API_KEY = "fake_key"
            em.RESEND_TEST_RECIPIENT = "developer@acme.com"
            with patch.object(em, "_domain_verified", return_value=False), \
                 patch.object(em, "_send_via_resend", return_value=True) as mock_r:
                result = em._send_email("developer@acme.com", "S", "B")
                mock_r.assert_called_once()
                assert result is True
        finally:
            em.RESEND_API_KEY = saved_key
            em.RESEND_TEST_RECIPIENT = saved_rec

    def test_no_test_recipient_set_skips_delivery(self):
        import auth.email as em
        saved_key, saved_rec = em.RESEND_API_KEY, em.RESEND_TEST_RECIPIENT
        try:
            em.RESEND_API_KEY = "fake_key"
            em.RESEND_TEST_RECIPIENT = ""
            with patch.object(em, "_domain_verified", return_value=False), \
                 patch.object(em, "_send_via_resend") as mock_r:
                result = em._send_email("any@example.com", "S", "B")
                mock_r.assert_not_called()
                assert result is False
        finally:
            em.RESEND_API_KEY = saved_key
            em.RESEND_TEST_RECIPIENT = saved_rec
