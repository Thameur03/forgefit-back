"""Pagination, User 360, privileged actions, audit, and operations tests."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect

from auth.utils import create_access_token, hash_password
from models.account_deletion import AccountDeletionChallenge
from models.admin_audit import AdminAuditEvent
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.user import User
from models.workout import Workout
from tests.support import TestingSessionLocal, client, test_engine


UTC = timezone.utc


def _create_user(
    email: str,
    *,
    role: str = "user",
    created_at: datetime | None = None,
    verified: bool = True,
) -> User:
    db = TestingSessionLocal()
    user = User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name=email.split("@")[0].replace(".", " ").title(),
        role=role,
        is_verified=verified,
        verified_at=created_at if verified else None,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.expunge(user)
    db.close()
    return user


def _headers(user: User) -> dict[str, str]:
    token = create_access_token({"sub": user.email, "ver": user.token_version or 0})
    return {"Authorization": f"Bearer {token}"}


def test_user_list_is_paginated_filterable_and_capped():
    admin = _create_user("owner@example.com", role="admin")
    _create_user("alpha@example.com", verified=True)
    _create_user("beta@example.com", verified=False)
    _create_user("gamma@example.com", verified=True)

    page = client.get(
        "/admin/users?page=2&page_size=2&role=user&sort_by=email&sort_order=asc",
        headers=_headers(admin),
    )
    filtered = client.get(
        "/admin/users?page=1&page_size=10&verified=false&search=beta",
        headers=_headers(admin),
    )
    oversized = client.get("/admin/users?page_size=101", headers=_headers(admin))
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 3
    assert page.json()["total_pages"] == 2
    assert [item["email"] for item in page.json()["items"]] == ["gamma@example.com"]
    assert filtered.status_code == 200
    assert [item["email"] for item in filtered.json()["items"]] == ["beta@example.com"]
    assert filtered.json()["items"][0]["is_verified"] is False
    assert oversized.status_code == 422


def test_normal_user_cannot_access_admin_operations():
    normal = _create_user("ordinary@example.com")
    headers = _headers(normal)
    for path in (
        "/admin/users",
        "/admin/audit-events",
        "/admin/operations/health",
        "/admin/security/status",
    ):
        assert client.get(path, headers=headers).status_code == 403


def test_user_360_is_operational_and_never_returns_secrets_or_health_values():
    admin = _create_user("overview-admin@example.com", role="admin")
    user = _create_user("overview-user@example.com", verified=False)
    db = TestingSessionLocal()
    db.add(
        Workout(
            user_id=user.id,
            date=date(2026, 1, 5),
            notes="private workout note",
            completed_at=datetime(2026, 1, 5, 12, tzinfo=UTC),
        )
    )
    db.add(
        Workout(
            user_id=user.id,
            date=date(2026, 1, 6),
            notes="private draft note",
        )
    )
    db.add(
        NutritionLog(
            user_id=user.id,
            date=date(2026, 1, 5),
            meal_name="private meal label",
            food_name="private food name",
            calories=900,
            protein_g=50,
        )
    )
    db.add(
        AnalyticsEvent(
            user_id=user.id,
            event_name="stats_viewed",
            event_category="insights",
            occurred_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
            platform="android",
            app_version="1.2.3",
            properties={
                "email": "private@example.com",
                "notes": "private event details",
            },
        )
    )
    db.add(
        AccountDeletionChallenge(
            user_id=user.id,
            code_hash="never-return-this-hash",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            failed_attempts=1,
        )
    )
    db.commit()
    db.close()

    response = client.get(
        f"/admin/users/{user.id}/overview", headers=_headers(admin)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    serialized = response.text
    assert payload["account"]["verified"] is False
    assert payload["account"]["latest_platform"] == "android"
    assert payload["engagement"]["completed_workouts"] == 1
    assert payload["engagement"]["nutrition_entries"] == 1
    assert payload["account_state"]["deletion_challenge_active"] is True
    assert "verification_code" not in serialized
    assert "reset_password" not in serialized
    assert "code_hash" not in serialized
    assert "never-return-this-hash" not in serialized
    assert "private workout note" not in serialized
    assert "private food name" not in serialized
    assert '"calories"' not in serialized
    assert '"weight_kg"' not in serialized
    assert "private@example.com" not in serialized
    assert "private event details" not in serialized


def test_superadmin_boundary_reauthentication_session_revocation_and_audit():
    superadmin = _create_user("super@example.com", role="superadmin")
    admin = _create_user("content-admin@example.com", role="admin")
    user = _create_user("managed@example.com")
    old_user_headers = _headers(user)

    denied = client.request(
        "DELETE",
        f"/admin/users/{user.id}",
        headers=_headers(admin),
        json={"current_password": "Password1", "confirmation": "DELETE"},
    )
    wrong_password = client.put(
        f"/admin/users/{user.id}/status",
        headers=_headers(superadmin),
        json={"current_password": "Wrongpass1", "status": "suspended"},
    )
    suspended = client.put(
        f"/admin/users/{user.id}/status",
        headers=_headers(superadmin),
        json={"current_password": "Password1", "status": "suspended"},
    )
    assert denied.status_code == 403
    assert wrong_password.status_code == 401
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["account_status"] == "suspended"
    assert client.get("/auth/me", headers=old_user_headers).status_code == 401

    reactivated = client.put(
        f"/admin/users/{user.id}/status",
        headers=_headers(superadmin),
        json={"current_password": "Password1", "status": "active"},
    )
    revoked = client.post(
        f"/admin/users/{user.id}/revoke-sessions",
        headers=_headers(superadmin),
        json={"current_password": "Password1"},
    )
    promoted = client.put(
        f"/admin/users/{user.id}/role",
        headers=_headers(superadmin),
        json={"current_password": "Password1", "role": "admin"},
    )
    assert reactivated.status_code == 200
    assert revoked.status_code == 200
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    audit = client.get(
        "/admin/audit-events?page=1&page_size=20", headers=_headers(superadmin)
    )
    assert audit.status_code == 200, audit.text
    actions = {item["action"] for item in audit.json()["items"]}
    assert {
        "user.suspended",
        "user.reactivated",
        "user.sessions_revoked",
        "user.role_changed",
    }.issubset(actions)


def test_first_superadmin_bootstrap_is_explicit_and_one_time():
    admin = _create_user("bootstrap-owner@example.com", role="admin")
    first = client.post(
        "/admin/security/bootstrap-superadmin",
        headers=_headers(admin),
        json={
            "current_password": "Password1",
            "confirmation": "MAKE_ME_SUPERADMIN",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["role"] == "superadmin"
    # A fresh token is required because the role is loaded from the DB, but the
    # JWT itself remains valid at the same token_version.
    db = TestingSessionLocal()
    promoted = db.query(User).filter(User.id == admin.id).one()
    headers = _headers(promoted)
    db.close()
    second = client.post(
        "/admin/security/bootstrap-superadmin",
        headers=headers,
        json={
            "current_password": "Password1",
            "confirmation": "MAKE_ME_SUPERADMIN",
        },
    )
    assert second.status_code == 409


def test_system_health_is_safe_and_admin_protected():
    admin = _create_user("health-admin@example.com", role="admin")
    response = client.get("/admin/operations/health", headers=_headers(admin))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["database"]["status"] == "healthy"
    assert isinstance(payload["database"]["query_latency_ms"], (int, float))
    serialized = response.text.lower()
    for secret_key in (
        "secret_key",
        "database_url",
        "api_key",
        "mail_password",
        "jwt",
        "token",
    ):
        assert secret_key not in serialized


def test_content_validation_draft_publish_and_audit():
    admin = _create_user("catalog-admin@example.com", role="admin")
    headers = _headers(admin)
    invalid_food = client.post(
        "/admin/foods",
        headers=headers,
        json={"name": "Bad", "calories": -1},
    )
    invalid_template = client.post(
        "/admin/program-templates",
        headers=headers,
        json={"name": "Empty published", "is_active": True},
    )
    draft = client.post(
        "/admin/program-templates",
        headers=headers,
        json={
            "name": "Production template",
            "description": "Draft first",
            "weeks": 4,
            "days_per_week": 1,
            "difficulty": "beginner",
            "goal": "strength",
        },
    )
    assert invalid_food.status_code == 422
    assert invalid_template.status_code == 422
    assert draft.status_code == 201, draft.text
    template_id = draft.json()["id"]
    assert draft.json()["is_active"] is False
    premature = client.put(
        f"/admin/program-templates/{template_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert premature.status_code == 409
    day = client.post(
        f"/admin/program-templates/{template_id}/days",
        headers=headers,
        json={"day_number": 1, "day_name": "Full body", "order_index": 0},
    )
    assert day.status_code == 201, day.text
    exercise = client.post(
        f"/admin/program-template-days/{day.json()['id']}/exercises",
        headers=headers,
        json={
            "exercise_name": "Squat",
            "exercise_id": "squat",
            "sets": 3,
            "reps": 8,
            "order_index": 0,
        },
    )
    assert exercise.status_code == 201, exercise.text
    published = client.put(
        f"/admin/program-templates/{template_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert published.status_code == 200, published.text
    assert published.json()["is_active"] is True
    db = TestingSessionLocal()
    actions = {row.action for row in db.query(AdminAuditEvent).all()}
    db.close()
    assert "program_template.created" in actions
    assert "program_template.exercise_created" in actions
    assert "program_template.published" in actions


def test_admin_content_uniqueness_is_enforced_by_database_indexes():
    expected = {
        "food_micronutrients": {
            "ux_food_micronutrients_food_nutrient",
        },
        "program_template_days": {
            "ux_program_template_days_number",
            "ux_program_template_days_order",
        },
        "program_template_exercises": {
            "ux_program_template_exercises_order",
        },
    }

    inspector = inspect(test_engine)
    for table, names in expected.items():
        indexes = {row["name"]: row for row in inspector.get_indexes(table)}
        assert names.issubset(indexes)
        assert all(indexes[name]["unique"] for name in names)


def test_duplicate_food_micronutrient_link_returns_clean_conflict():
    admin = _create_user("link-admin@example.com", role="admin")
    headers = _headers(admin)
    food = client.post(
        "/admin/foods",
        headers=headers,
        json={"name": "Indexed food", "calories": 100},
    )
    nutrient = client.post(
        "/admin/micronutrients",
        headers=headers,
        json={"name": "Indexed nutrient", "unit": "mg"},
    )
    assert food.status_code == nutrient.status_code == 201
    payload = {
        "micronutrient_id": nutrient.json()["id"],
        "amount": 2.5,
        "unit": "mg",
    }

    first = client.post(
        f"/admin/foods/{food.json()['id']}/micronutrients",
        headers=headers,
        json=payload,
    )
    duplicate = client.post(
        f"/admin/foods/{food.json()['id']}/micronutrients",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 201, first.text
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == (
        "This food already has that micronutrient link"
    )


def test_catalog_integrity_errors_are_clean_and_linked_nutrients_delete_safely():
    admin = _create_user("catalog-integrity-admin@example.com", role="admin")
    headers = _headers(admin)

    first_category = client.post(
        "/admin/food-categories",
        headers=headers,
        json={"name": "Unique category"},
    )
    duplicate_category = client.post(
        "/admin/food-categories",
        headers=headers,
        json={"name": "Unique category"},
    )
    assert first_category.status_code == 201, first_category.text
    assert duplicate_category.status_code == 409
    assert duplicate_category.json()["detail"] == (
        "A food category with that name already exists"
    )

    food = client.post(
        "/admin/foods",
        headers=headers,
        json={"name": "Linked food", "calories": 100},
    )
    nutrient = client.post(
        "/admin/micronutrients",
        headers=headers,
        json={"name": "Linked nutrient", "unit": "mg"},
    )
    assert food.status_code == nutrient.status_code == 201
    link = client.post(
        f"/admin/foods/{food.json()['id']}/micronutrients",
        headers=headers,
        json={
            "micronutrient_id": nutrient.json()["id"],
            "amount": 1,
            "unit": "mg",
        },
    )
    assert link.status_code == 201, link.text

    deleted = client.delete(
        f"/admin/micronutrients/{nutrient.json()['id']}",
        headers=headers,
    )
    remaining_links = client.get(
        f"/admin/foods/{food.json()['id']}/micronutrients",
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert remaining_links.status_code == 200
    assert remaining_links.json()["items"] == []
