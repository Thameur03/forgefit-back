"""Forensic regression matrix for deterministic Lab Insights V2."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from models.nutrition import NutritionDayStatus, NutritionLog
from models.program import Program, ProgramDay
from models.schedule import ScheduledWorkout
from models.user import User
from models.workout import Workout, WorkoutSet
from services.lab_insights_v2 import LabInsightsV2Engine, exact_program_opportunities
from tests.support import TestingSessionLocal


NOW = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
TODAY = NOW.date()


def _user(db, suffix: str = "user", *, weight: float | None = 80) -> User:
    user = User(
        email=f"{suffix}@example.com",
        hashed_password="not-used",
        full_name="Lab Tester",
        is_verified=True,
        weight_kg=weight,
        timezone="UTC",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _workout(
    db,
    user: User,
    days_ago: int,
    *,
    completed: bool = True,
    exercise_id: str | None = "bench-press",
    name: str = "Bench Press",
    weight: float | None = 80,
    reps: int = 8,
    sets: int = 3,
    scheduled_id: int | None = None,
) -> Workout:
    workout = Workout(
        user_id=user.id,
        date=TODAY - timedelta(days=days_ago),
        completed_at=(NOW - timedelta(days=days_ago)) if completed else None,
        completion_inferred=False,
        duration_seconds=3600,
        scheduled_workout_id=scheduled_id,
    )
    db.add(workout)
    db.flush()
    db.add(
        WorkoutSet(
            workout_id=workout.id,
            exercise_name=name,
            exercise_id=exercise_id,
            sets=sets,
            reps=reps,
            weight_kg=weight,
        )
    )
    db.commit()
    return workout


def _nutrition_day(
    db,
    user: User,
    days_ago: int,
    *,
    complete: bool,
    calories: float = 2200,
    protein: float | None = 150,
) -> None:
    target = TODAY - timedelta(days=days_ago)
    db.add(
        NutritionLog(
            user_id=user.id,
            date=target,
            meal_name="Dinner",
            food_name="Recorded meal",
            calories=calories,
            protein_g=protein,
            carbs_g=200,
            fat_g=70,
        )
    )
    if complete:
        db.add(
            NutritionDayStatus(
                user_id=user.id,
                date=target,
                is_complete=True,
                completed_at=NOW - timedelta(days=days_ago),
                updated_at=NOW - timedelta(days=days_ago),
            )
        )
    db.commit()


def _generate(db, user: User, *, force: bool = True) -> dict:
    return LabInsightsV2Engine(db, user, now=NOW).generate(force_refresh=force)


def _finding(snapshot: dict, detector_id: str) -> dict | None:
    return next(
        (item for item in snapshot["insights"] if item["detector_id"] == detector_id),
        None,
    )


def test_a_new_user_has_explicit_unavailable_domains_and_null_metrics():
    db = TestingSessionLocal()
    user = _user(db, "new")
    result = _generate(db, user)

    assert result["schema_version"] == "2.0"
    assert result["metrics"]["workload"]["change_percent"] is None
    assert result["metrics"]["nutrition"]["average_complete_day_calories"] is None
    assert any(item["status"] == "Unavailable" for item in result["domain_coverage"])
    assert not any("score" in key.lower() for key in result["metrics"])
    db.close()


def test_drafts_and_in_progress_shells_do_not_affect_any_training_output():
    db = TestingSessionLocal()
    user = _user(db, "draft")
    _workout(db, user, 1, completed=False, weight=500)
    result = _generate(db, user)

    assert result["metrics"]["training_frequency"]["recent_sessions"] == 0
    assert result["metrics"]["performance"]["exercises_observed"] == 0
    assert result["metrics"]["workload"]["recent_comparable_workload_kg"] is None
    assert not any(item["detector_id"] == "personal_record" for item in result["insights"])
    db.close()


def test_missing_nutrition_is_unknown_not_zero_or_diet_judgement():
    db = TestingSessionLocal()
    user = _user(db, "unknown-nutrition")
    result = _generate(db, user)
    metric = result["metrics"]["nutrition"]

    assert metric["average_complete_day_calories"] is None
    assert metric["average_complete_day_protein_g"] is None
    rendered = json.dumps(result, default=str).lower()
    assert "0 calories" not in rendered
    assert "poor nutrition" not in rendered
    assert "urgently" not in rendered
    assert "optimal range" not in rendered
    db.close()


def test_partial_logged_day_is_not_complete_day_intake():
    db = TestingSessionLocal()
    user = _user(db, "partial")
    _nutrition_day(db, user, 0, complete=False, calories=120)
    result = _generate(db, user)

    assert result["metrics"]["nutrition"]["days_with_any_logs"] == 1
    assert result["metrics"]["nutrition"]["complete_days"] == 0
    assert result["metrics"]["nutrition"]["average_complete_day_calories"] is None
    db.close()


def test_complete_day_with_unknown_protein_suppresses_protein_conclusion():
    db = TestingSessionLocal()
    user = _user(db, "unknown-protein")
    user.protein_target_g = 140
    db.commit()
    for day in range(3):
        _nutrition_day(db, user, day, complete=True, protein=None)
    result = _generate(db, user)

    metric = result["metrics"]["nutrition"]
    assert metric["average_complete_day_protein_g"] is None
    assert metric["protein_target_eligible_days"] == 0
    assert _finding(result, "nutrition_protein_target") is None
    db.close()


def test_f_and_f2_missing_bodyweight_cannot_improve_analytical_status():
    db = TestingSessionLocal()
    known = _user(db, "weight-known", weight=80)
    missing = _user(db, "weight-missing", weight=None)
    for user in (known, missing):
        for day in range(4):
            _workout(db, user, day * 3, weight=80 + day)
            _nutrition_day(db, user, day, complete=True, calories=2200, protein=120)

    known_result = _generate(db, known)
    missing_result = _generate(db, missing)
    known_buckets = sorted((x["detector_id"], x["bucket"]) for x in known_result["insights"])
    missing_buckets = sorted((x["detector_id"], x["bucket"]) for x in missing_result["insights"])

    assert missing_buckets == known_buckets
    assert missing_result["metrics"] == known_result["metrics"]
    assert any("weight-relative" in item for item in missing_result["limitations"])
    db.close()


def test_return_after_inactivity_has_null_workload_delta_not_999():
    db = TestingSessionLocal()
    user = _user(db, "return")
    _workout(db, user, 1, weight=100)
    _workout(db, user, 100, weight=90)
    result = _generate(db, user)

    assert result["metrics"]["workload"]["change_percent"] is None
    assert "999" not in json.dumps(result, default=str)
    rhythm = _finding(result, "training_frequency")
    assert rhythm is not None
    assert "resumed" in rhythm["title"].lower()
    db.close()


@pytest.mark.parametrize("days,expected", [(7, 2), (14, 4), (30, 7)])
def test_program_denominator_uses_exact_eligible_opportunities(days: int, expected: int):
    start = TODAY - timedelta(days=days - 1)
    rows = [
        SimpleNamespace(
            scheduled_date=TODAY - timedelta(days=offset),
            linkage_trustworthy=True,
            status="completed" if offset % 2 == 0 else "planned",
        )
        for offset in (0, 3, 7, 10, 14, 21, 28)
    ]
    # Cancelled and legacy-untrusted rows never enter the denominator.
    rows.extend(
        [
            SimpleNamespace(scheduled_date=TODAY, linkage_trustworthy=True, status="cancelled"),
            SimpleNamespace(scheduled_date=TODAY, linkage_trustworthy=False, status="completed"),
        ]
    )
    _, eligible = exact_program_opportunities(rows, start, TODAY)
    assert eligible == expected


def test_program_execution_uses_linked_completed_opportunities():
    db = TestingSessionLocal()
    user = _user(db, "program")
    program = Program(user_id=user.id, name="A", is_active=True, days_per_week=3, activated_at=NOW)
    db.add(program)
    db.flush()
    day = ProgramDay(program_id=program.id, day_number=1, day_name="Day A")
    db.add(day)
    db.flush()
    schedules = []
    for offset in (2, 5, 9):
        row = ScheduledWorkout(
            user_id=user.id, program_id=program.id, program_day_id=day.id,
            scheduled_date=TODAY - timedelta(days=offset), status="planned",
            linkage_trustworthy=True,
        )
        db.add(row)
        db.flush()
        schedules.append(row)
    db.commit()
    _workout(db, user, 2, scheduled_id=schedules[0].id)
    schedules[0].status = "completed"
    schedules[0].completed_at = NOW - timedelta(days=2)
    db.commit()

    result = _generate(db, user)
    metric = result["metrics"]["program_execution"]
    assert metric["eligible_opportunities"] == 3
    assert metric["matched_completed_opportunities"] == 1
    assert metric["execution_percent"] == pytest.approx(33.3)
    db.close()


def test_j_long_history_recent_decline_uses_personal_baseline():
    db = TestingSessionLocal()
    user = _user(db, "decline")
    for day in (25, 30, 35, 40, 45, 50, 55, 60, 65, 70):
        _workout(db, user, day)
    _workout(db, user, 5)
    result = _generate(db, user)

    finding = _finding(result, "training_frequency")
    assert finding is not None
    assert finding["bucket"] == "Watch"
    assert finding["evidence"]["baseline"] is not None
    assert finding["confidence"]["level"] in {"Medium", "High"}
    db.close()


def test_exercise_progression_and_genuine_pr_are_positive():
    db = TestingSessionLocal()
    user = _user(db, "progress")
    for days_ago, weight in ((70, 80), (60, 82), (50, 84), (10, 90), (3, 95)):
        _workout(db, user, days_ago, weight=weight, reps=8)
    result = _generate(db, user)

    positives = [item for item in result["insights"] if item["bucket"] == "Positive Trend"]
    assert positives
    assert any(
        item["detector_id"] in {"personal_record", "exercise_progression"}
        for item in positives
    ), [(item["detector_id"], item["bucket"], item["priority_score"]) for item in result["insights"]]
    db.close()


def test_plateau_requires_four_comparable_sessions_across_28_days():
    db = TestingSessionLocal()
    user = _user(db, "plateau")
    for days_ago in (1, 12, 23, 34):
        _workout(db, user, days_ago, weight=100, reps=8)
    result = _generate(db, user)

    plateau = _finding(result, "plateau_candidate")
    assert plateau is not None
    assert "stable across 4 comparable sessions" in plateau["observation"]
    assert "cause" not in plateau["observation"].lower()
    db.close()


def test_extreme_outlier_is_excluded_from_performance_and_workload():
    db = TestingSessionLocal()
    user = _user(db, "outlier")
    _workout(db, user, 1, weight=1500)
    result = _generate(db, user)

    assert result["metrics"]["performance"]["excluded_set_count"] == 1
    assert result["metrics"]["workload"]["excluded_set_count"] == 1
    assert result["metrics"]["workload"]["recent_comparable_workload_kg"] is None
    db.close()


def test_timezone_boundary_uses_users_local_calendar_day():
    db = TestingSessionLocal()
    user = _user(db, "timezone")
    user.timezone = "Pacific/Kiritimati"
    db.commit()
    instant = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)  # Sep 2 locally
    result = LabInsightsV2Engine(db, user, now=instant).generate(force_refresh=True)

    assert str(result["period"]["recent"]["end"]) == "2026-09-02"
    assert result["user_timezone"] == "Pacific/Kiritimati"
    db.close()


def test_no_readiness_recovery_score_or_causal_claim_leaks_into_v2():
    db = TestingSessionLocal()
    user = _user(db, "claims")
    _workout(db, user, 1)
    result = _generate(db, user)
    rendered = json.dumps(result, default=str).lower()

    assert "recovery_score" not in rendered
    assert "readiness" not in rendered
    assert "caused your" not in rendered
    assert "overtrained" not in rendered
    assert "hormonal" not in rendered
    db.close()


def test_snapshot_cache_hits_then_invalidates_on_source_change():
    db = TestingSessionLocal()
    user = _user(db, "cache")
    first = _generate(db, user)
    hit = LabInsightsV2Engine(db, user, now=NOW + timedelta(minutes=1)).generate()
    assert hit["cache_status"] == "hit"
    assert hit["analysis_id"] == first["analysis_id"]

    _workout(db, user, 1)
    refreshed = LabInsightsV2Engine(db, user, now=NOW + timedelta(minutes=2)).generate()
    assert refreshed["cache_status"] == "generated"
    assert refreshed["source_data_watermark"] != first["source_data_watermark"]
    db.close()


def test_lifecycle_new_ongoing_resolved_and_reopened_is_stable():
    db = TestingSessionLocal()
    user = _user(db, "lifecycle")
    first = _generate(db, user)
    first_item = _finding(first, "nutrition_complete_day_coverage")
    assert first_item and first_item["lifecycle"] == "New"

    ongoing = _generate(db, user)
    ongoing_item = _finding(ongoing, "nutrition_complete_day_coverage")
    assert ongoing_item and ongoing_item["lifecycle"] == "Ongoing"
    assert ongoing_item["id"] == first_item["id"]

    for day in range(3):
        _nutrition_day(db, user, day, complete=True)
    resolved = _generate(db, user)
    assert any(
        item["id"] == first_item["id"] and item["lifecycle"] == "Resolved"
        for item in resolved["resolved_insights"]
    )

    db.query(NutritionDayStatus).update({NutritionDayStatus.is_complete: False})
    db.commit()
    reopened = _generate(db, user)
    reopened_item = _finding(reopened, "nutrition_complete_day_coverage")
    assert reopened_item and reopened_item["lifecycle"] == "Reopened"
    db.close()


@pytest.mark.parametrize(
    "name,workout_days,nutrition_days",
    [
        ("consistent_beginner", [2, 6, 10, 14], []),
        ("consistent_lifter", [1, 4, 8, 12, 16, 25, 32, 39, 46, 53, 60, 67], [0, 1, 2, 3, 4]),
        ("previously_consistent_inactive", [30, 37, 44, 51, 58, 65], []),
        ("frequent_no_progression", [1, 5, 9, 13, 17, 25, 32, 39], []),
        ("nutrition_focused", [], [0, 1, 2, 3, 4, 5]),
        ("incomplete_profile", [2], []),
    ],
)
def test_persona_matrix_never_fabricates_scores(
    name: str, workout_days: list[int], nutrition_days: list[int]
):
    db = TestingSessionLocal()
    user = _user(db, name, weight=None if name == "incomplete_profile" else 80)
    for day in workout_days:
        _workout(db, user, day)
    for day in nutrition_days:
        _nutrition_day(db, user, day, complete=True)
    result = _generate(db, user)
    rendered = json.dumps(result, default=str).lower()

    assert result["schema_version"] == "2.0"
    assert "overall_score" not in rendered
    assert "excellent readiness" not in rendered
    assert all(item["confidence"]["level"] in {"High", "Medium", "Low", "Insufficient"} for item in result["insights"])
    db.close()
