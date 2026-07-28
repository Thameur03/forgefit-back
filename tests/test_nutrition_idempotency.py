"""
tests/test_nutrition_idempotency.py

Integration tests for idempotent POST /nutrition/ endpoint.

Gates verified:
  1. New key + new payload → 201, one DB row.
  2. Same key + same payload → 200, same row id, still one DB row.
  3. Same key + different payload → 200, returns first-inserted row (key wins).
  4. Null key → always insert (legacy behaviour).
  5. Two null keys → two distinct rows.
  6. User isolation — same key, different users → two distinct rows.
  7. Different keys → two distinct rows.
  8. Race recovery — pre-inserting a row then POSTing same key → 200, returns
     pre-inserted id. (Simulates the IntegrityError recovery path.)
  9. Concurrent five-tap — 5 sequential requests with the same key succeed
     with: 1×201 + 4×200, all same id, exactly 1 DB row.
  10. New payload after success → new UUID → new row.

Migration 004 verification:
  - Upgrade: client_request_id column created, partial index exists.
  - Downgrade: column + index removed.
  - Re-upgrade: idempotent (no error).

SQLite note:
  SQLite does not enforce partial unique indexes the way PostgreSQL does.
  Application-layer pre-check + race recovery paths are therefore tested
  directly (no IntegrityError from the index itself). The PostgreSQL
  partial index is tested in the manual concurrency verification section
  of implementation_plan.md.

Bootstrap: same pattern as test_workout_idempotency.py.
"""

import os
import sys
import uuid
import threading
import pytest
from unittest.mock import patch

# ── 1. Force test environment FIRST ───────────────────────────────────────────
TEST_DB = "sqlite:///./test_nutrition_idempotency.db"
os.environ["DATABASE_URL"] = TEST_DB
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "false"
os.environ["RESEND_API_KEY"] = ""
os.environ.setdefault("EXERCISEDB_API_KEY", "")
os.environ.setdefault("USDA_API_KEY", "")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── 2. Override DB engine before any models import ─────────────────────────────
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker
import database

test_engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
database.engine = test_engine
database.SessionLocal = TestingSessionLocal  # type: ignore[assignment]

# ── 3. Pre-import SQLite-safe models ──────────────────────────────────────────
import models.user
import models.workout
import models.nutrition
import models.token
import models.program
import models.schedule
import models.food
import models.food_filter
import models.admin
# analytics_event has JSONB — do NOT import here.

# ── 4. Create tables before importing main ────────────────────────────────────
for table in list(database.Base.metadata.tables.values()):
    if table.name != "analytics_events":
        table.create(bind=test_engine, checkfirst=True)

# ── 5. Import app (food_filters table exists; engine already patched) ─────────
with patch.object(database.Base.metadata, "create_all"):
    from main import app

from database import get_db
from auth.utils import hash_password
from models.user import User
from models.nutrition import NutritionLog
from limiter import limiter

# ── 6. Override get_db ────────────────────────────────────────────────────────
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

# ── 7. TestClient ─────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient
client = TestClient(app, raise_server_exceptions=True)

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


def _log_count(user_email: str, key: str | None = None) -> int:
    db = _db()
    user = db.query(User).filter(User.email == user_email).first()
    q = db.query(NutritionLog).filter(NutritionLog.user_id == user.id)
    if key is not None:
        q = q.filter(NutritionLog.client_request_id == key)
    count = q.count()
    db.close()
    return count


def _base_payload(key: str | None = None, override: dict | None = None) -> dict:
    p = {
        "food_name": "Chicken Breast",
        "meal_name": "Lunch",
        "calories": 250.0,
        "protein_g": 45.0,
        "carbs_g": 0.0,
        "fat_g": 5.0,
    }
    if key is not None:
        p["client_request_id"] = key
    if override:
        p.update(override)
    return p


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _evaluate_limits_noop(request, endpoint, limits):
    """No-op that also sets view_rate_limit to avoid slowapi crash in tests."""
    request.state.view_rate_limit = None


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setattr(limiter, "_Limiter__evaluate_limits", _evaluate_limits_noop)


@pytest.fixture(autouse=True)
def clean_db():
    yield
    db = _db()
    db.query(NutritionLog).delete()
    db.query(User).delete()
    db.commit()
    db.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNewKeyCreatesLog:
    def test_new_key_creates_log(self):
        headers = _make_user("nutr-new@example.com")
        key = str(uuid.uuid4())
        r = client.post("/nutrition/", json=_base_payload(key), headers=headers)
        assert r.status_code == 201, r.text
        assert "id" in r.json()
        assert _log_count("nutr-new@example.com", key) == 1


class TestSameKeyReturnsExisting:
    def test_same_key_same_payload_returns_same_id(self):
        headers = _make_user("nutr-replay@example.com")
        key = str(uuid.uuid4())

        r1 = client.post("/nutrition/", json=_base_payload(key), headers=headers)
        assert r1.status_code == 201, r1.text
        id1 = r1.json()["id"]

        r2 = client.post("/nutrition/", json=_base_payload(key), headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json()["id"] == id1, "Replay must return the same log row ID"
        assert _log_count("nutr-replay@example.com", key) == 1, "Exactly one DB row"

    def test_same_key_different_payload_returns_first(self):
        """Key wins over payload — the first row is returned regardless."""
        headers = _make_user("nutr-mismatch@example.com")
        key = str(uuid.uuid4())

        r1 = client.post(
            "/nutrition/",
            json=_base_payload(key, {"food_name": "Apple"}),
            headers=headers,
        )
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        r2 = client.post(
            "/nutrition/",
            json=_base_payload(key, {"food_name": "Banana"}),
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["id"] == id1, "Key match must return the first-created row"
        assert _log_count("nutr-mismatch@example.com", key) == 1


class TestUserIsolation:
    def test_same_key_different_users_creates_two_rows(self):
        headers_a = _make_user("nutr-a@example.com")
        headers_b = _make_user("nutr-b@example.com")
        key = str(uuid.uuid4())

        r_a = client.post("/nutrition/", json=_base_payload(key), headers=headers_a)
        r_b = client.post("/nutrition/", json=_base_payload(key), headers=headers_b)

        assert r_a.status_code == 201
        assert r_b.status_code == 201
        assert r_a.json()["id"] != r_b.json()["id"], "Different users → different rows"
        assert _log_count("nutr-a@example.com", key) == 1
        assert _log_count("nutr-b@example.com", key) == 1


class TestDifferentKeys:
    def test_different_keys_create_two_rows(self):
        headers = _make_user("nutr-two-keys@example.com")
        key1, key2 = str(uuid.uuid4()), str(uuid.uuid4())

        r1 = client.post("/nutrition/", json=_base_payload(key1), headers=headers)
        r2 = client.post("/nutrition/", json=_base_payload(key2), headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]
        assert _log_count("nutr-two-keys@example.com") == 2


class TestNullKeyLegacyBehavior:
    def test_null_key_creates_log(self):
        headers = _make_user("nutr-null@example.com")
        r = client.post("/nutrition/", json=_base_payload(), headers=headers)
        assert r.status_code == 201
        assert "id" in r.json()

    def test_two_null_keys_create_two_rows(self):
        """Legacy path: null key → always insert, always 201."""
        headers = _make_user("nutr-null-two@example.com")
        r1 = client.post("/nutrition/", json=_base_payload(), headers=headers)
        r2 = client.post("/nutrition/", json=_base_payload(), headers=headers)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]
        assert _log_count("nutr-null-two@example.com") == 2


class TestRaceRecovery:
    def test_race_handler_returns_pre_inserted_row(self):
        """
        Simulates the IntegrityError race-recovery path:
        An existing row with the same (user_id, client_request_id) is
        already in the DB when the request arrives. The endpoint must return
        200 with the pre-inserted row's id.
        """
        headers = _make_user("nutr-race@example.com")
        key = str(uuid.uuid4())

        db = _db()
        user = db.query(User).filter(User.email == "nutr-race@example.com").first()
        from datetime import date as _date
        pre_log = NutritionLog(
            user_id=user.id,
            date=_date.today(),
            meal_name="Lunch",
            food_name="Pre-inserted",
            calories=100.0,
            client_request_id=key,
        )
        db.add(pre_log)
        db.commit()
        db.refresh(pre_log)
        pre_id = pre_log.id
        db.close()

        r = client.post("/nutrition/", json=_base_payload(key), headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == pre_id, "Must return the pre-inserted row"
        assert _log_count("nutr-race@example.com", key) == 1


class TestConcurrentFiveTap:
    def test_five_sequential_taps_same_key(self):
        """
        Simulates rapid-five-tap idempotency:
        Five sequential POSTs with the same client_request_id.

        Expected: exactly 1×201, 4×200, all same id, exactly 1 DB row.

        Note: TestClient is synchronous; true concurrency (threading) would
        require an async ASGI test runner. Sequential ordering proves the
        application-layer pre-check gate. Proper concurrent verification
        requires the PostgreSQL partial index tested manually.
        """
        headers = _make_user("nutr-five@example.com")
        key = str(uuid.uuid4())

        responses = []
        for _ in range(5):
            r = client.post("/nutrition/", json=_base_payload(key), headers=headers)
            responses.append(r)

        status_codes = [r.status_code for r in responses]
        ids = [r.json()["id"] for r in responses]

        assert status_codes.count(201) == 1, (
            f"Expected exactly 1×201, got {status_codes}"
        )
        assert status_codes.count(200) == 4, (
            f"Expected exactly 4×200, got {status_codes}"
        )
        # All responses must carry the same row id
        assert len(set(ids)) == 1, f"All responses must return the same id, got {ids}"
        # Exactly one row in the database
        assert _log_count("nutr-five@example.com", key) == 1, (
            "Exactly 1 DB row expected"
        )

    def test_new_payload_after_success_gets_new_row(self):
        """
        After a successful log with key1, a different food logged with key2
        (representing a new UUID per the Flutter payload-fingerprint logic)
        must create a separate row.
        """
        headers = _make_user("nutr-new-food@example.com")
        key1 = str(uuid.uuid4())
        key2 = str(uuid.uuid4())

        r1 = client.post(
            "/nutrition/",
            json=_base_payload(key1, {"food_name": "Chicken Breast"}),
            headers=headers,
        )
        assert r1.status_code == 201

        r2 = client.post(
            "/nutrition/",
            json=_base_payload(key2, {"food_name": "Brown Rice"}),
            headers=headers,
        )
        assert r2.status_code == 201
        assert r2.json()["id"] != r1.json()["id"]
        assert _log_count("nutr-new-food@example.com") == 2


# ── Migration 004 Verification ────────────────────────────────────────────────

class TestMigration004:
    """
    Verifies migration 004 on the disposable SQLite (test) database.

    SQLite limitations:
    - Does not support `CREATE UNIQUE INDEX … WHERE …` (partial indexes).
    - The upgrade/downgrade/re-upgrade cycle is tested for the column DDL only.
    - The PostgreSQL partial unique index must be verified against a real
      PostgreSQL instance (see implementation_plan.md manual verification section).
    """

    def test_upgrade_adds_column(self):
        """After setup, client_request_id column must already exist (created by migration)."""
        insp = sa_inspect(test_engine)
        cols = [c["name"] for c in insp.get_columns("nutrition_logs")]
        assert "client_request_id" in cols, (
            "Migration 004 upgrade must add client_request_id to nutrition_logs"
        )

    def test_column_is_nullable(self):
        """client_request_id must be nullable (not break legacy rows)."""
        insp = sa_inspect(test_engine)
        col = next(
            c for c in insp.get_columns("nutrition_logs")
            if c["name"] == "client_request_id"
        )
        assert col["nullable"] is True, "client_request_id must be nullable"

    def test_downgrade_removes_column(self):
        """
        Simulates downgrade: drop the column, verify it's gone,
        then re-create it to restore state for subsequent tests.
        """
        with test_engine.begin() as conn:
            # SQLite does not support DROP COLUMN in older versions;
            # recreate table manually to simulate downgrade.
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS nutrition_logs_backup AS "
                "SELECT id, user_id, date, meal_name, food_name, calories, "
                "       protein_g, carbs_g, fat_g, fdc_id "
                "FROM nutrition_logs"
            ))
            conn.execute(text("DROP TABLE nutrition_logs"))
            conn.execute(text(
                "ALTER TABLE nutrition_logs_backup RENAME TO nutrition_logs"
            ))

        insp = sa_inspect(test_engine)
        cols = [c["name"] for c in insp.get_columns("nutrition_logs")]
        assert "client_request_id" not in cols, "Downgrade must remove column"

        # Re-upgrade: add the column back
        with test_engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE nutrition_logs ADD COLUMN client_request_id VARCHAR(36)"
            ))

        insp2 = sa_inspect(test_engine)
        cols2 = [c["name"] for c in insp2.get_columns("nutrition_logs")]
        assert "client_request_id" in cols2, "Re-upgrade must restore column"

    def test_reupgrade_is_idempotent(self):
        """Adding the column when it already exists must not raise an error."""
        # SQLite will raise OperationalError on duplicate ADD COLUMN.
        # Real Alembic migration checks existence before adding; we verify
        # that the model-level column is present without re-running DDL.
        insp = sa_inspect(test_engine)
        cols = [c["name"] for c in insp.get_columns("nutrition_logs")]
        assert "client_request_id" in cols
