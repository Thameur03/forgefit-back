"""Deterministic product analytics and admin authorization tests."""

from datetime import date, datetime, timedelta, timezone

import pytest

from auth.utils import create_access_token, hash_password
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.program import Program, ProgramDay
from models.schedule import ScheduledWorkout
from models.user import User
from models.workout import Workout, WorkoutSet
from tests.support import TestingSessionLocal, client


UTC = timezone.utc


def _user(
    email: str,
    *,
    role: str = "user",
    created: datetime,
    verified: bool = True,
) -> User:
    return User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name=email.split("@")[0],
        role=role,
        is_verified=verified,
        verified_at=created if verified else None,
        created_at=created,
    )


def _headers(user: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token({"sub": user.email, "ver": user.token_version or 0})
    }


def _seed_admin_and_user() -> tuple[dict[str, str], dict[str, str]]:
    db = TestingSessionLocal()
    created = datetime(2025, 1, 1, tzinfo=UTC)
    admin = _user("admin@example.com", role="admin", created=created)
    normal = _user("normal@example.com", created=created)
    db.add_all([admin, normal])
    db.commit()
    admin_headers = _headers(admin)
    normal_headers = _headers(normal)
    db.close()
    return admin_headers, normal_headers


@pytest.mark.parametrize(
    "path",
    [
        "/admin/analytics/overview",
        "/admin/analytics/growth",
        "/admin/analytics/funnel",
        "/admin/analytics/retention",
        "/admin/analytics/features",
        "/admin/analytics/workouts",
        "/admin/analytics/nutrition",
        "/admin/analytics/programs",
        "/admin/analytics/scheduling",
        "/admin/analytics/insights",
        "/admin/analytics/events",
        "/admin/analytics/errors",
    ],
)
def test_every_analytics_endpoint_rejects_normal_users(path: str):
    _, normal_headers = _seed_admin_and_user()
    assert client.get(path, headers=normal_headers).status_code == 403


def test_true_cohort_retention_uses_exact_calendar_days_and_maturity():
    db = TestingSessionLocal()
    admin = _user(
        "retention-admin@example.com",
        role="admin",
        created=datetime(2025, 1, 1, tzinfo=UTC),
    )
    first = _user("first@example.com", created=datetime(2026, 1, 1, 10, tzinfo=UTC))
    second = _user("second@example.com", created=datetime(2026, 1, 1, 22, tzinfo=UTC))
    third = _user("third@example.com", created=datetime(2026, 1, 10, tzinfo=UTC))
    db.add_all([admin, first, second, third])
    db.flush()
    for workout_date in (
        date(2026, 1, 2),
        date(2026, 1, 8),
        date(2026, 1, 15),
        date(2026, 1, 31),
    ):
        db.add(
            Workout(
                user_id=first.id,
                date=workout_date,
                duration_seconds=1200,
                completed_at=datetime.combine(
                    workout_date, datetime.min.time(), tzinfo=UTC
                )
                + timedelta(hours=12),
            )
        )
    db.add(
        Workout(
            user_id=second.id,
            date=date(2026, 1, 2),
            duration_seconds=600,
            completed_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        )
    )
    db.commit()
    headers = _headers(admin)
    db.close()

    response = client.get(
        "/admin/analytics/retention?start_date=2026-01-01&end_date=2026-01-31",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    summary = {row["day"]: row for row in payload["summary"]}
    assert summary[1] == {
        "day": 1,
        "eligible_users": 3,
        "retained_users": 2,
        "rate": 66.67,
    }
    assert summary[7]["eligible_users"] == 3
    assert summary[7]["retained_users"] == 1
    assert summary[14]["eligible_users"] == 3
    assert summary[14]["retained_users"] == 1
    assert summary[30]["eligible_users"] == 2
    assert summary[30]["retained_users"] == 1
    assert payload["dau"] == 1
    assert payload["wau"] == 1
    assert payload["mau"] == 2
    assert payload["dau_mau_stickiness"] == 50.0
    assert payload["wau_mau_stickiness"] == 50.0
    assert "exactly UTC calendar day n" in payload["semantics"]


def test_signup_funnel_stitches_identity_and_is_monotonic():
    db = TestingSessionLocal()
    admin = _user(
        "funnel-admin@example.com",
        role="admin",
        created=datetime(2025, 1, 1, tzinfo=UTC),
    )
    converted = _user(
        "converted@example.com", created=datetime(2026, 2, 1, tzinfo=UTC)
    )
    db.add_all([admin, converted])
    db.flush()
    event_time = datetime(2026, 2, 1, 12, tzinfo=UTC)
    for name in (
        "signup_started",
        "signup_summary_viewed",
        "signup_submit_clicked",
        "signup_completed",
        "email_verification_completed",
        "onboarding_completed",
    ):
        db.add(
            AnalyticsEvent(
                user_id=converted.id,
                anonymous_id="anonymous-identity-0001",
                session_id="session-identity-0000001",
                event_name=name,
                event_category="auth",
                occurred_at=event_time,
            )
        )
    # A second session reaches only the summary.
    for name in ("signup_started", "signup_summary_viewed"):
        db.add(
            AnalyticsEvent(
                anonymous_id="anonymous-identity-0002",
                session_id="session-identity-0000002",
                event_name=name,
                event_category="auth",
                occurred_at=event_time,
            )
        )
    db.add(
        Workout(
            user_id=converted.id,
            date=date(2026, 2, 2),
            completed_at=datetime(2026, 2, 2, 12, tzinfo=UTC),
        )
    )
    db.add(
        Workout(
            user_id=converted.id,
            date=date(2026, 2, 8),
            completed_at=datetime(2026, 2, 8, 12, tzinfo=UTC),
        )
    )
    db.commit()
    headers = _headers(admin)
    db.close()

    response = client.get(
        "/admin/analytics/funnel?start_date=2026-02-01&end_date=2026-02-07",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    stages = response.json()["stages"]
    counts = [stage["count"] for stage in stages]
    assert counts == [2, 2, 1, 1, 1, 1, 1, 1]
    assert counts == sorted(counts, reverse=True)
    assert stages[-1]["eligible_count"] == 1
    assert stages[-1]["conversion_from_previous"] == 100.0


def test_overview_and_domain_endpoints_use_transactional_records():
    db = TestingSessionLocal()
    admin = _user(
        "domain-admin@example.com",
        role="admin",
        created=datetime(2025, 1, 1, tzinfo=UTC),
    )
    user = _user("domain-user@example.com", created=datetime(2026, 3, 1, tzinfo=UTC))
    db.add_all([admin, user])
    db.flush()
    workout = Workout(
        user_id=user.id,
        date=date(2026, 3, 5),
        duration_seconds=1800,
        completed_at=datetime(2026, 3, 5, 12, tzinfo=UTC),
    )
    db.add(workout)
    db.add(
        Workout(
            user_id=user.id,
            date=date(2026, 3, 6),
            duration_seconds=0,
        )
    )
    db.flush()
    db.add(
        WorkoutSet(
            workout_id=workout.id,
            exercise_name="Bench Press",
            exercise_id="bench-press",
            sets=3,
            reps=10,
            weight_kg=50,
        )
    )
    for food, meal in (("Egg", "breakfast"), ("Oats", "breakfast"), ("Rice", "dinner")):
        db.add(
            NutritionLog(
                user_id=user.id,
                date=date(2026, 3, 5),
                meal_name=meal,
                food_name=food,
                calories=100,
                fdc_id=123 if food == "Egg" else None,
            )
        )
    program = Program(
        user_id=user.id,
        name="Private custom program",
        is_active=True,
        source_template="strength_starter",
    )
    db.add(program)
    db.flush()
    day = ProgramDay(program_id=program.id, day_number=1, day_name="Day 1")
    db.add(day)
    db.flush()
    scheduled = ScheduledWorkout(
        user_id=user.id,
        program_id=program.id,
        program_day_id=day.id,
        scheduled_date=date(2026, 3, 5),
        created_at=datetime(2026, 3, 4, tzinfo=UTC),
    )
    db.add(scheduled)
    db.flush()
    for event_name, occurred_at in (
        ("workout_scheduled", datetime(2026, 3, 4, 9, tzinfo=UTC)),
        ("scheduled_workout_completed", datetime(2026, 3, 5, 10, tzinfo=UTC)),
    ):
        db.add(
            AnalyticsEvent(
                user_id=user.id,
                event_name=event_name,
                event_category="scheduling",
                properties={"schedule_id": scheduled.id},
                occurred_at=occurred_at,
            )
        )
    # An unrelated completion cannot inflate this cohort above 100%.
    db.add(
        AnalyticsEvent(
            user_id=user.id,
            event_name="scheduled_workout_completed",
            event_category="scheduling",
            properties={"schedule_id": scheduled.id + 9999},
            occurred_at=datetime(2026, 3, 5, 11, tzinfo=UTC),
        )
    )
    db.commit()
    headers = _headers(admin)
    db.close()
    query = "?start_date=2026-03-01&end_date=2026-03-10"

    overview = client.get("/admin/analytics/overview" + query, headers=headers)
    workouts = client.get("/admin/analytics/workouts" + query, headers=headers)
    nutrition = client.get("/admin/analytics/nutrition" + query, headers=headers)
    programs = client.get("/admin/analytics/programs" + query, headers=headers)
    scheduling = client.get("/admin/analytics/scheduling" + query, headers=headers)
    for response in (overview, workouts, nutrition, programs, scheduling):
        assert response.status_code == 200, response.text
    assert overview.json()["metrics"]["total_users"]["value"] == 1.0
    assert overview.json()["metrics"]["workouts_completed"]["value"] == 1.0
    assert overview.json()["metrics"]["meals_logged"]["value"] == 2.0
    assert workouts.json()["completed_workouts"] == 1
    assert workouts.json()["total_sets"] == 3
    assert workouts.json()["total_training_volume_kg"] == 1500.0
    assert workouts.json()["top_exercises"][0]["key"] == "bench-press"
    assert len(workouts.json()["top_exercises"]) == 1
    assert nutrition.json()["nutrition_entries"] == 3
    assert nutrition.json()["meals_logged"] == 2
    assert programs.json()["active_programs"] == 1
    assert programs.json()["users_with_active_program"] == 1
    assert scheduling.json()["scheduled_workouts"] == 1
    assert scheduling.json()["completed_events"] == 1
    assert scheduling.json()["scheduled_to_completed_rate"] == 100.0


def test_events_are_paginated_and_errors_match_response_contract():
    db = TestingSessionLocal()
    admin = _user(
        "events-admin@example.com",
        role="admin",
        created=datetime(2025, 1, 1, tzinfo=UTC),
    )
    user = _user("events-user@example.com", created=datetime(2026, 4, 1, tzinfo=UTC))
    db.add_all([admin, user])
    db.flush()
    for index in range(3):
        db.add(
            AnalyticsEvent(
                user_id=user.id,
                event_name="food_search_failed",
                event_category="nutrition",
                properties={"error_code": "provider_timeout"},
                occurred_at=datetime(2026, 4, 1, 12, index, tzinfo=UTC),
            )
        )
    db.commit()
    headers = _headers(admin)
    db.close()

    events = client.get(
        "/admin/analytics/events?start_date=2026-04-01&end_date=2026-04-02&page=2&page_size=2",
        headers=headers,
    )
    errors = client.get(
        "/admin/analytics/errors?start_date=2026-04-01&end_date=2026-04-02",
        headers=headers,
    )
    assert events.status_code == errors.status_code == 200
    assert events.json()["total"] == 3
    assert events.json()["total_pages"] == 2
    assert len(events.json()["items"]) == 1
    item = errors.json()["items"][0]
    assert item["event_name"] == "food_search_failed"
    assert item["error_code"] == "provider_timeout"
    assert item["count"] == 3
    assert item["unique_users"] == 1
    assert item["last_occurred"].startswith("2026-04-01T12:02")


def test_legacy_event_properties_are_resanitized_before_admin_output():
    db = TestingSessionLocal()
    admin = _user(
        "privacy-admin@example.com",
        role="admin",
        created=datetime(2025, 1, 1, tzinfo=UTC),
    )
    user = _user(
        "privacy-user@example.com",
        created=datetime(2026, 4, 1, tzinfo=UTC),
    )
    db.add_all([admin, user])
    db.flush()
    db.add(
        AnalyticsEvent(
            user_id=user.id,
            event_name="meal_logged",
            event_category="nutrition",
            properties={
                "meal_type": "lunch",
                "email": "private@example.com",
                "notes": "private freeform text",
                "weight_kg": 90,
            },
            occurred_at=datetime(2026, 4, 1, 12, tzinfo=UTC),
        )
    )
    db.commit()
    headers = _headers(admin)
    db.close()

    response = client.get(
        "/admin/analytics/events?start_date=2026-04-01&end_date=2026-04-02",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["properties"] == {"meal_type": "lunch"}
    assert "private@example.com" not in response.text
    assert "private freeform text" not in response.text
