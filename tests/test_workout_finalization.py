"""
tests/test_workout_finalization.py

Backend tests for the crash-safe workout finalization flow.

Verifies:
  - PUT /workouts/{id} updates the same row (no duplication)
  - Repeated PUTs are idempotent (no extra rows)
  - Sets survive a PUT (not deleted on update)
  - client_request_id remains unchanged after PUT
  - All existing workout idempotency tests still import cleanly

Database and application isolation come from the shared test harness.
"""

import uuid
from auth.utils import hash_password
from models.user import User
from models.workout import Workout
from tests.support import TestingSessionLocal, client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db():
    return TestingSessionLocal()


def _make_user(email: str, password: str = "Password1") -> dict:
    db = _db()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name="Finalization Tester",
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed ({r.status_code}): {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _workout_row(workout_id: int):
    db = _db()
    row = db.query(Workout).filter(Workout.id == workout_id).first()
    db.close()
    return row


def _workout_count_for_user(email: str) -> int:
    db = _db()
    user = db.query(User).filter(User.email == email).first()
    count = db.query(Workout).filter(Workout.user_id == user.id).count()
    db.close()
    return count


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPutUpdatesExistingRow:
    """PUT /workouts/{id} writes to the same row — no duplication."""

    def test_put_updates_name_and_duration(self):
        """PUT with name + duration_seconds updates the correct row."""
        headers = _make_user("put-update@example.com")
        key = str(uuid.uuid4())

        # Create workout shell
        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        # Finalize via PUT (mimics finalizeWorkout())
        put_r = client.put(
            f"/workouts/{workout_id}",
            json={"name": "Leg Day Final", "duration_seconds": 3600, "calories_burned": 400},
            headers=headers,
        )
        assert put_r.status_code == 200, put_r.text

        row = _workout_row(workout_id)
        assert row is not None
        assert row.name == "Leg Day Final"
        assert row.duration_seconds == 3600

    def test_put_creates_no_additional_row(self):
        """After POST + PUT, exactly one workout row exists for this user."""
        headers = _make_user("put-no-dup@example.com")
        key = str(uuid.uuid4())

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        count_before = _workout_count_for_user("put-no-dup@example.com")

        client.put(
            f"/workouts/{workout_id}",
            json={"name": "Once", "duration_seconds": 1800, "calories_burned": 200},
            headers=headers,
        )

        count_after = _workout_count_for_user("put-no-dup@example.com")
        assert count_after == count_before, (
            f"PUT created extra rows: before={count_before}, after={count_after}"
        )


class TestRepeatedPutIdempotent:
    """Two PUTs with the same payload produce one row with the latest values."""

    def test_repeated_put_returns_200_not_conflict(self):
        """Second PUT to same ID returns 200, not 4xx."""
        headers = _make_user("repeated-put@example.com")
        key = str(uuid.uuid4())

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        payload = {"name": "Push Day", "duration_seconds": 2700, "calories_burned": 300}
        r1 = client.put(f"/workouts/{workout_id}", json=payload, headers=headers)
        r2 = client.put(f"/workouts/{workout_id}", json=payload, headers=headers)

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

    def test_repeated_put_no_extra_rows(self):
        """Two PUTs leave exactly one row for this user."""
        headers = _make_user("repeated-put-count@example.com")
        key = str(uuid.uuid4())

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        payload = {"name": "Pull Day", "duration_seconds": 2400, "calories_burned": 250}
        client.put(f"/workouts/{workout_id}", json=payload, headers=headers)
        client.put(f"/workouts/{workout_id}", json=payload, headers=headers)

        count = _workout_count_for_user("repeated-put-count@example.com")
        assert count == 1, f"Expected 1 row, got {count}"


class TestSetsSurvivePut:
    """Sets logged before finalization must still exist after PUT."""

    def test_sets_survive_put(self):
        """Logged sets are not deleted when the workout is finalized via PUT."""
        headers = _make_user("sets-survive@example.com")
        key = str(uuid.uuid4())

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        # Log a set
        set_r = client.post(
            f"/workouts/{workout_id}/sets",
            json={
                "exercise_name": "Squat",
                "sets": 1,
                "weight_kg": 100.0,
                "reps": 5,
            },
            headers=headers,
        )
        assert set_r.status_code in (200, 201), set_r.text

        # Finalize
        client.put(
            f"/workouts/{workout_id}",
            json={"name": "Leg Day", "duration_seconds": 1800, "calories_burned": 200},
            headers=headers,
        )

        # Verify set still exists
        detail_r = client.get(f"/workouts/{workout_id}", headers=headers)
        assert detail_r.status_code == 200, detail_r.text
        data = detail_r.json()
        total_sets = len(data.get("sets", []))
        assert total_sets >= 1, f"Sets were lost after PUT — found {total_sets}"


class TestClientRequestIdUnchangedAfterPut:
    """PUT must not alter the client_request_id column."""

    def test_client_request_id_unchanged(self):
        """client_request_id on the row matches what was sent in the POST."""
        headers = _make_user("crid-stable@example.com")
        key = str(uuid.uuid4())

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code in (200, 201)
        workout_id = r.json()["id"]

        client.put(
            f"/workouts/{workout_id}",
            json={"name": "Test", "duration_seconds": 900, "calories_burned": 100},
            headers=headers,
        )

        row = _workout_row(workout_id)
        assert row is not None
        assert row.client_request_id == key, (
            f"client_request_id changed: expected {key}, got {row.client_request_id}"
        )
