from unittest.mock import patch
from uuid import uuid4

from models.analytics_event import AnalyticsEvent
from models.user import User
from routers import auth as auth_router
from tests.support import TestingSessionLocal, client

DATA = {"email": "verify@example.com", "password": "Password1", "full_name": "Verify User"}


def test_register_verify_login_flow(monkeypatch):
    monkeypatch.setattr(auth_router, "REQUIRE_EMAIL_VERIFICATION", True)
    anonymous_id = str(uuid4())
    session_id = str(uuid4())
    for event_name in (
        "signup_started",
        "signup_summary_viewed",
        "signup_submit_clicked",
    ):
        event = client.post(
            "/analytics/events/public",
            json={
                "event_name": event_name,
                "anonymous_id": anonymous_id,
                "session_id": session_id,
                "client_event_id": str(uuid4()),
            },
        )
        assert event.status_code == 202
    registration_data = {
        **DATA,
        "analytics_anonymous_id": anonymous_id,
        "analytics_session_id": session_id,
    }
    delivered = {}
    def capture(_email, code):
        delivered["code"] = code
        return True
    with patch.object(auth_router, "email_delivery_configured", return_value=True), patch.object(
        auth_router, "send_verification_email", side_effect=capture
    ):
        registered = client.post("/auth/register", json=registration_data)
    assert registered.status_code == 201
    assert registered.json()["is_verified"] is False
    assert client.post("/auth/login", json={"email": DATA["email"], "password": DATA["password"]}).status_code == 403
    assert client.post(
        "/auth/verify-email",
        json={
            "email": DATA["email"],
            "code": delivered["code"],
            "analytics_anonymous_id": anonymous_id,
            "analytics_session_id": session_id,
        },
    ).status_code == 200
    db = TestingSessionLocal()
    user = db.query(User).filter(User.email == DATA["email"]).one()
    analytics_rows = db.query(AnalyticsEvent).order_by(AnalyticsEvent.id).all()
    assert {row.user_id for row in analytics_rows} == {user.id}
    assert [row.event_name for row in analytics_rows] == [
        "signup_started",
        "signup_summary_viewed",
        "signup_submit_clicked",
        "signup_completed",
        "onboarding_completed",
        "email_verification_completed",
    ]
    db.close()
    login = client.post("/auth/login", json={"email": DATA["email"], "password": DATA["password"]})
    assert login.status_code == 200
    assert login.json()["access_token"] and login.json()["refresh_token"]


def test_registration_rejects_partial_analytics_identity():
    response = client.post(
        "/auth/register",
        json={**DATA, "analytics_anonymous_id": str(uuid4())},
    )
    assert response.status_code == 422


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
