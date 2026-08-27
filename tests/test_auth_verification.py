from unittest.mock import patch

from models.user import User
from routers import auth as auth_router
from tests.support import TestingSessionLocal, client

DATA = {"email": "verify@example.com", "password": "Password1", "full_name": "Verify User"}


def test_register_verify_login_flow(monkeypatch):
    monkeypatch.setattr(auth_router, "REQUIRE_EMAIL_VERIFICATION", True)
    delivered = {}
    def capture(_email, code):
        delivered["code"] = code
        return True
    with patch.object(auth_router, "email_delivery_configured", return_value=True), patch.object(
        auth_router, "send_verification_email", side_effect=capture
    ):
        registered = client.post("/auth/register", json=DATA)
    assert registered.status_code == 201
    assert registered.json()["is_verified"] is False
    assert client.post("/auth/login", json={"email": DATA["email"], "password": DATA["password"]}).status_code == 403
    assert client.post("/auth/verify-email", json={"email": DATA["email"], "code": delivered["code"]}).status_code == 200
    login = client.post("/auth/login", json={"email": DATA["email"], "password": DATA["password"]})
    assert login.status_code == 200
    assert login.json()["access_token"] and login.json()["refresh_token"]


def test_registration_rolls_back_if_email_delivery_fails(monkeypatch):
    monkeypatch.setattr(auth_router, "REQUIRE_EMAIL_VERIFICATION", True)
    with patch.object(auth_router, "email_delivery_configured", return_value=True), patch.object(
        auth_router, "send_verification_email", return_value=False
    ):
        response = client.post("/auth/register", json=DATA)
    assert response.status_code == 503
    assert "Resend" not in response.text
    db = TestingSessionLocal()
    assert db.query(User).filter(User.email == DATA["email"]).count() == 0
    db.close()
