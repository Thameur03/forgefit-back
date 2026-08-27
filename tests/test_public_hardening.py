import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from auth import email as email_service
from auth.utils import hash_password
from config import parse_cors_origins, require_email_verification
from models.account_deletion import AccountDeletionChallenge
from models.user import User
from routers import account as account_router
from tests.support import TestingSessionLocal, client


def _create_user(email: str) -> int:
    db = TestingSessionLocal()
    user = User(email=email, hashed_password=hash_password("Password1"), full_name="Web User", is_verified=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.id
    db.close()
    return user_id


def test_public_pages_are_unauthenticated_branded_and_safe():
    privacy = client.get("/privacy")
    deletion = client.get("/delete-account")
    assert privacy.status_code == deletion.status_code == 200
    assert "Jugurtha Fit Privacy Policy" in privacy.text
    assert "Knowing an email address alone can never delete" in deletion.text
    assert deletion.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in privacy.headers["content-security-policy"]
    assert client.get("/health").json() == {"status": "ok"}


def test_public_deletion_requires_delivered_email_code():
    user_id = _create_user("web-delete@example.com")
    delivered = {}
    def capture(_email, code):
        delivered["code"] = code
        return True
    with patch.object(account_router, "email_delivery_configured", return_value=True), patch.object(
        account_router, "send_account_deletion_email", side_effect=capture
    ):
        requested = client.post("/account/deletion/request", json={"email": "web-delete@example.com"})
    assert requested.status_code == 202
    wrong = client.post("/account/deletion/confirm", json={"email": "web-delete@example.com", "code": "000000", "confirmation": "DELETE"})
    assert wrong.status_code == 400
    confirmed = client.post("/account/deletion/confirm", json={"email": "web-delete@example.com", "code": delivered["code"], "confirmation": "DELETE"})
    assert confirmed.status_code == 200
    db = TestingSessionLocal()
    assert db.query(User).filter(User.id == user_id).count() == 0
    db.close()


def test_unknown_email_response_is_generic_and_origin_is_checked():
    with patch.object(account_router, "email_delivery_configured", return_value=True), patch.object(
        account_router, "send_account_deletion_email"
    ) as sender:
        response = client.post("/account/deletion/request", json={"email": "unknown@example.com"})
    assert response.status_code == 202
    sender.assert_not_called()
    rejected = client.post("/account/deletion/request", headers={"Origin": "https://attacker.example"}, json={"email": "unknown@example.com"})
    assert rejected.status_code == 403


def test_challenge_is_hashed_and_expires():
    user_id = _create_user("expired@example.com")
    db = TestingSessionLocal()
    db.add(AccountDeletionChallenge(user_id=user_id, code_hash=hash_password("123456"), expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    db.close()
    response = client.post("/account/deletion/confirm", json={"email": "expired@example.com", "code": "123456", "confirmation": "DELETE"})
    assert response.status_code == 400


@pytest.mark.parametrize("value", ["*", "https://ok.example,", "http://localhost:3000", "http://public.example", "https://public.example/path"])
def test_production_rejects_bad_cors(value):
    with pytest.raises(RuntimeError):
        parse_cors_origins(value, app_env="production")


def test_production_forces_verification(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    assert require_email_verification() is True


def test_development_or_test_keeps_openapi_available():
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_production_disables_api_documentation_in_fresh_process():
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "sqlite://",
            "SECRET_KEY": "isolated-production-config-test-secret-32-plus",
            "AUTO_CREATE_TABLES": "false",
            "CORS_ORIGINS": "https://app.example.com",
            "REQUIRE_EMAIL_VERIFICATION": "true",
            "RESEND_API_KEY": "",
            "MAIL_USERNAME": "",
            "MAIL_PASSWORD": "",
        }
    )
    script = """
from fastapi.testclient import TestClient
from main import app
client = TestClient(app)
assert client.get('/docs').status_code == 404
assert client.get('/redoc').status_code == 404
assert client.get('/openapi.json').status_code == 404
assert client.get('/health').json() == {'status': 'ok'}
assert client.get('/privacy').status_code == 200
assert client.get('/delete-account').status_code == 200
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_production_cors_parsing_trims_and_deduplicates():
    assert parse_cors_origins(
        " https://app.example.com,https://www.example.com,https://app.example.com ",
        app_env="production",
    ) == ["https://app.example.com", "https://www.example.com"]


def test_production_smtp_requires_encrypted_transport(monkeypatch):
    monkeypatch.setattr(email_service, "MAIL_USERNAME", "sender@example.com")
    monkeypatch.setattr(email_service, "MAIL_PASSWORD", "configured-password")
    monkeypatch.setattr(email_service, "MAIL_FROM", "Jugurtha Fit <sender@example.com>")
    monkeypatch.setattr(email_service, "MAIL_STARTTLS", False)
    monkeypatch.setattr(email_service, "MAIL_SSL_TLS", False)
    monkeypatch.setattr(email_service, "is_production", lambda: True)
    assert email_service._can_send_smtp() is False

    monkeypatch.setattr(email_service, "MAIL_STARTTLS", True)
    assert email_service._can_send_smtp() is True
