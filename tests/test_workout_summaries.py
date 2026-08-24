"""Regression coverage for metadata returned by GET /workouts/."""

from auth.utils import hash_password
from models.user import User
from tests.support import TestingSessionLocal, client


def _login(email: str) -> dict[str, str]:
    db = TestingSessionLocal()
    db.add(
        User(
            email=email,
            hashed_password=hash_password("Password1"),
            full_name="Summary Tester",
            is_verified=True,
        )
    )
    db.commit()
    db.close()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "Password1"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_list_exposes_existing_aggregate_and_exercise_metadata():
    headers = _login("summary-metadata@example.com")
    created = client.post(
        "/workouts/",
        json={"name": "Upper Body", "duration_seconds": 1800},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    workout_id = created.json()["id"]

    for payload in [
        {"exercise_name": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 80},
        {"exercise_name": "Bench Press", "sets": 1, "reps": 6, "weight_kg": 90},
        {"exercise_name": "Cable Curl", "sets": 2, "reps": 12, "weight_kg": 20},
    ]:
        response = client.post(
            f"/workouts/{workout_id}/sets",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 201, response.text

    response = client.get("/workouts/", headers=headers)
    assert response.status_code == 200, response.text
    summary = response.json()[0]

    assert summary["total_sets"] == 6
    assert summary["total_volume_kg"] == 2940
    assert summary["duration_seconds"] == 1800
    assert summary["exercise_count"] == 2
    assert summary["exercise_names"] == ["Bench Press", "Cable Curl"]


def test_list_keeps_legacy_missing_duration_honest():
    headers = _login("summary-legacy@example.com")
    created = client.post("/workouts/", json={"name": "Legacy"}, headers=headers)
    assert created.status_code == 201, created.text

    response = client.get("/workouts/", headers=headers)
    assert response.status_code == 200, response.text
    summary = response.json()[0]

    assert summary["duration_seconds"] == 0
    assert summary["exercise_count"] == 0
    assert summary["exercise_names"] == []
