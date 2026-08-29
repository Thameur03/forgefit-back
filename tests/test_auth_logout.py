"""Security and contract tests for POST /auth/logout."""

from jose import jwt

from auth.utils import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    create_refresh_token,
    hash_password,
)
from models.token import RevokedToken
from models.analytics_event import AnalyticsEvent
from models.user import User
from tests.support import TestingSessionLocal, client


def _session_tokens(email: str) -> tuple[str, str, int]:
    db = TestingSessionLocal()
    user = User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name="Logout Tester",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return (
        create_access_token({"sub": email}),
        create_refresh_token({"sub": email}),
        user_id,
    )


def _jti(token: str) -> str:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["jti"]


def test_logout_revokes_matching_access_and_refresh_tokens():
    access_token, refresh_token, user_id = _session_tokens("logout@example.com")

    response = client.post(
        "/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200, response.text
    db = TestingSessionLocal()
    revoked = {
        row.token_jti: row.user_id for row in db.query(RevokedToken).all()
    }
    user = db.query(User).filter(User.id == user_id).one()
    logout_events = (
        db.query(AnalyticsEvent)
        .filter(
            AnalyticsEvent.user_id == user_id,
            AnalyticsEvent.event_name == "logout_completed",
        )
        .count()
    )
    db.close()
    assert revoked[_jti(access_token)] == user_id
    assert revoked[_jti(refresh_token)] == user_id
    assert user.last_logout_at is not None
    assert logout_events == 1

    rejected = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert rejected.status_code == 401


def test_logout_rejects_refresh_token_owned_by_another_user():
    access_token, own_refresh, _ = _session_tokens("owner@example.com")
    _, other_refresh, _ = _session_tokens("other@example.com")

    response = client.post(
        "/auth/logout",
        json={"refresh_token": other_refresh},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 401
    db = TestingSessionLocal()
    revoked_jtis = {row.token_jti for row in db.query(RevokedToken).all()}
    db.close()
    assert _jti(access_token) not in revoked_jtis
    assert _jti(own_refresh) not in revoked_jtis
    assert _jti(other_refresh) not in revoked_jtis


def test_logout_requires_refresh_token_body():
    access_token, _, _ = _session_tokens("missing-body@example.com")
    response = client.post(
        "/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 422


def test_refresh_token_cannot_authenticate_protected_endpoint():
    _, refresh_token, _ = _session_tokens("refresh-as-access@example.com")
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert response.status_code == 401


def test_refresh_rotation_still_revokes_old_token_and_issues_typed_tokens():
    _, refresh_token, user_id = _session_tokens("rotation@example.com")

    response = client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    decoded_access = jwt.decode(
        payload["access_token"], SECRET_KEY, algorithms=[ALGORITHM]
    )
    decoded_refresh = jwt.decode(
        payload["refresh_token"], SECRET_KEY, algorithms=[ALGORITHM]
    )
    assert decoded_access["type"] == "access"
    assert decoded_refresh["type"] == "refresh"

    db = TestingSessionLocal()
    revoked = db.query(RevokedToken).filter(
        RevokedToken.token_jti == _jti(refresh_token)
    ).one()
    db.close()
    assert revoked.user_id == user_id


def test_logout_does_not_log_token_values(caplog):
    access_token, refresh_token, _ = _session_tokens("secrecy@example.com")
    with caplog.at_level("DEBUG"):
        response = client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert response.status_code == 200
    assert access_token not in caplog.text
    assert refresh_token not in caplog.text
