"""Contract, privacy, idempotency, and identity tests for analytics ingest."""

from datetime import datetime, timezone
from uuid import uuid4

from auth.utils import create_access_token, hash_password
from models.analytics_event import AnalyticsEvent
from models.user import User
from tests.support import TestingSessionLocal, client
from limiter import limiter


def _user_headers(email: str = "analytics-user@example.com") -> tuple[dict[str, str], int]:
    db = TestingSessionLocal()
    user = User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name="Analytics User",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": user.email})
    user_id = user.id
    db.close()
    return {"Authorization": f"Bearer {token}"}, user_id


def _identity() -> tuple[str, str]:
    return str(uuid4()), str(uuid4())


def test_server_recorded_signup_completion_cannot_be_fabricated_by_client():
    anonymous_id, session_id = _identity()
    response = client.post(
        "/analytics/events/public",
        json={
            "event_name": "sign_up_completed",
            "anonymous_id": anonymous_id,
            "session_id": session_id,
            "client_event_id": str(uuid4()),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 422, response.text
    headers, _ = _user_headers("server-event-user@example.com")
    authenticated = client.post(
        "/analytics/events",
        headers=headers,
        json={
            "event_name": "onboarding_completed",
            "session_id": session_id,
            "client_event_id": str(uuid4()),
        },
    )
    assert authenticated.status_code == 422, authenticated.text
    db = TestingSessionLocal()
    count = db.query(AnalyticsEvent).count()
    db.close()
    assert count == 0


def test_workout_alias_and_private_properties_are_normalized():
    headers, user_id = _user_headers()
    response = client.post(
        "/analytics/events",
        headers=headers,
        json={
            "event_name": "workout_logged",
            "event_category": "forged-category",
            "session_id": str(uuid4()),
            "client_event_id": str(uuid4()),
            "properties": {
                "set_count": 12,
                "duration_bucket": "30_45",
                "email": "must-not-survive@example.com",
                "notes": "private",
                "arbitrary_health_field": {"weight": 80},
            },
        },
    )
    assert response.status_code == 202, response.text
    db = TestingSessionLocal()
    event = db.query(AnalyticsEvent).one()
    db.close()
    assert event.user_id == user_id
    assert event.event_name == "workout_completed"
    assert event.event_category == "workout"
    assert event.properties == {"set_count": 12, "duration_bucket": "30_45"}


def test_client_event_id_makes_network_retry_idempotent():
    headers, _ = _user_headers()
    event_id = str(uuid4())
    payload = {
        "event_name": "meal_logged",
        "session_id": str(uuid4()),
        "client_event_id": event_id,
        "properties": {"meal_type": "lunch", "has_macros": True},
    }
    first = client.post("/analytics/events", headers=headers, json=payload)
    second = client.post("/analytics/events", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    db = TestingSessionLocal()
    assert db.query(AnalyticsEvent).count() == 1
    db.close()


def test_identity_link_is_exact_session_scoped_and_cannot_reassign():
    anonymous_id, session_id = _identity()
    other_session = str(uuid4())
    for session in (session_id, other_session):
        response = client.post(
            "/analytics/events/public",
            json={
                "event_name": "signup_started",
                "anonymous_id": anonymous_id,
                "session_id": session,
                "client_event_id": str(uuid4()),
            },
        )
        assert response.status_code == 202

    headers, user_id = _user_headers()
    linked = client.post(
        "/analytics/identity/link",
        headers=headers,
        json={"anonymous_id": anonymous_id, "session_id": session_id},
    )
    assert linked.status_code == 200
    assert linked.json() == {"linked_events": 1}

    other_headers, other_user_id = _user_headers("other-analytics-user@example.com")
    reassignment = client.post(
        "/analytics/identity/link",
        headers=other_headers,
        json={"anonymous_id": anonymous_id, "session_id": session_id},
    )
    assert reassignment.status_code == 200
    assert reassignment.json() == {"linked_events": 0}

    db = TestingSessionLocal()
    rows = {
        row.session_id: row.user_id
        for row in db.query(AnalyticsEvent).order_by(AnalyticsEvent.id).all()
    }
    db.close()
    assert rows[session_id] == user_id
    assert rows[session_id] != other_user_id
    assert rows[other_session] is None


def test_ingest_rejects_unknown_events_unsafe_shapes_and_old_timestamps():
    anonymous_id, session_id = _identity()
    base = {"anonymous_id": anonymous_id, "session_id": session_id}
    unknown = client.post(
        "/analytics/events/public",
        json={**base, "event_name": "made_up_conversion"},
    )
    nested = client.post(
        "/analytics/events/public",
        json={
            **base,
            "event_name": "signup_started",
            "properties": {"utm_source": {"secret": "nested"}},
        },
    )
    extra = client.post(
        "/analytics/events/public",
        json={**base, "event_name": "signup_started", "password": "Password1"},
    )
    old = client.post(
        "/analytics/events/public",
        json={
            **base,
            "event_name": "signup_started",
            "occurred_at": "2020-01-01T00:00:00Z",
        },
    )
    assert unknown.status_code == 422
    # Nested values are safely dropped rather than persisted.
    assert nested.status_code == 202
    assert extra.status_code == 422
    assert old.status_code == 422
    db = TestingSessionLocal()
    only = db.query(AnalyticsEvent).one()
    db.close()
    assert only.properties is None


def test_content_length_guard_rejects_oversized_analytics_payload():
    anonymous_id, session_id = _identity()
    response = client.post(
        "/analytics/events/public",
        content=(
            '{"event_name":"signup_started","anonymous_id":"'
            + anonymous_id
            + '","session_id":"'
            + session_id
            + '","padding":"'
            + ("x" * 17_000)
            + '"}'
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_streamed_payload_without_content_length_is_still_bounded():
    anonymous_id, session_id = _identity()
    body = (
        '{"event_name":"signup_started","anonymous_id":"'
        + anonymous_id
        + '","session_id":"'
        + session_id
        + '","padding":"'
        + ("x" * 17_000)
        + '"}'
    ).encode()

    response = client.post(
        "/analytics/events/public",
        content=(chunk for chunk in (body[:9000], body[9000:])),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_public_ingestion_has_a_real_rate_limit():
    anonymous_id, session_id = _identity()
    payload = {
        "event_name": "signup_started",
        "anonymous_id": anonymous_id,
        "session_id": session_id,
        "client_event_id": str(uuid4()),
    }
    limiter.reset()
    limiter.enabled = True
    responses = [
        client.post("/analytics/events/public", json=payload) for _ in range(61)
    ]
    assert all(response.status_code == 202 for response in responses[:60])
    assert responses[-1].status_code == 429
