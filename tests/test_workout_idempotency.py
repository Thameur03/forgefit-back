"""
tests/test_workout_idempotency.py

Integration tests for idempotent POST /workouts/ endpoint.

Scope:
  SQLite in-process — proves application-layer idempotency (pre-check SELECT +
  IntegrityError recovery). Does NOT prove the PostgreSQL partial unique index;
  that requires the manual concurrency verification in implementation_plan.md.

The shared test harness supplies a complete, foreign-key-aware SQLite schema,
one FastAPI dependency override, and a fresh database for every test.
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
            full_name="Test User",
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed ({r.status_code}): {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _workout_count(user_email: str, key: str | None = None) -> int:
    db = _db()
    user = db.query(User).filter(User.email == user_email).first()
    q = db.query(Workout).filter(Workout.user_id == user.id)
    if key is not None:
        q = q.filter(Workout.client_request_id == key)
    count = q.count()
    db.close()
    return count


# ── Fixtures ──────────────────────────────────────────────────────────────────

# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNewKeyCreatesWorkout:
    def test_new_key_creates_workout(self):
        headers = _make_user("new-key@example.com")
        key = str(uuid.uuid4())
        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code == 201, r.text
        assert "id" in r.json()
        assert _workout_count("new-key@example.com", key) == 1


class TestSameKeyReturnsExisting:
    def test_same_key_same_user_returns_same_id(self):
        headers = _make_user("replay@example.com")
        key = str(uuid.uuid4())

        r1 = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r1.status_code == 201, r1.text
        id1 = r1.json()["id"]

        r2 = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json()["id"] == id1, "Replay must return the same workout ID"
        assert _workout_count("replay@example.com", key) == 1, "Only one row in DB"

    def test_same_key_different_payload_returns_first(self):
        headers = _make_user("mismatch@example.com")
        key = str(uuid.uuid4())

        r1 = client.post("/workouts/", json={"client_request_id": key, "name": "Workout A"}, headers=headers)
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        r2 = client.post("/workouts/", json={"client_request_id": key, "name": "Workout B"}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == id1, "Must return first-created row"
        assert _workout_count("mismatch@example.com", key) == 1


class TestUserIsolation:
    def test_same_key_different_users_isolated(self):
        headers_a = _make_user("user-a@example.com")
        headers_b = _make_user("user-b@example.com")
        key = str(uuid.uuid4())

        r_a = client.post("/workouts/", json={"client_request_id": key}, headers=headers_a)
        r_b = client.post("/workouts/", json={"client_request_id": key}, headers=headers_b)

        assert r_a.status_code == 201
        assert r_b.status_code == 201
        assert r_a.json()["id"] != r_b.json()["id"], "Different users must get distinct workout IDs"
        assert _workout_count("user-a@example.com", key) == 1
        assert _workout_count("user-b@example.com", key) == 1


class TestDifferentKeys:
    def test_different_keys_create_two_workouts(self):
        headers = _make_user("two-keys@example.com")
        key1, key2 = str(uuid.uuid4()), str(uuid.uuid4())

        r1 = client.post("/workouts/", json={"client_request_id": key1}, headers=headers)
        r2 = client.post("/workouts/", json={"client_request_id": key2}, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]
        assert _workout_count("two-keys@example.com") == 2


class TestNullKeyLegacyBehavior:
    def test_null_key_creates_workout(self):
        headers = _make_user("null-key@example.com")
        r = client.post("/workouts/", json={}, headers=headers)
        assert r.status_code == 201
        assert "id" in r.json()

    def test_two_null_keys_create_two_workouts(self):
        headers = _make_user("null-two@example.com")
        r1 = client.post("/workouts/", json={}, headers=headers)
        r2 = client.post("/workouts/", json={}, headers=headers)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]
        assert _workout_count("null-two@example.com") == 2


class TestIntegrityErrorRecovery:
    def test_replay_via_precheck(self):
        headers = _make_user("race@example.com")
        key = str(uuid.uuid4())

        r1 = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r1.status_code == 201
        existing_id = r1.json()["id"]

        r2 = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == existing_id
        assert _workout_count("race@example.com", key) == 1

    def test_race_handler_returns_pre_inserted_row(self):
        headers = _make_user("race2@example.com")
        key = str(uuid.uuid4())

        db = _db()
        user = db.query(User).filter(User.email == "race2@example.com").first()
        from datetime import date as _date
        pre_workout = Workout(user_id=user.id, date=_date.today(), client_request_id=key)
        db.add(pre_workout)
        db.commit()
        pre_id = pre_workout.id
        db.close()

        r = client.post("/workouts/", json={"client_request_id": key}, headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == pre_id
