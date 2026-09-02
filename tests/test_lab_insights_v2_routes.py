"""Database/route integration tests without the repository's stalled TestClient.

Dependencies are supplied exactly as FastAPI would supply them. The response is
validated against the public Pydantic contract, while all writes use real ORM
tables and foreign-key enforcement.
"""

from datetime import date, datetime, timezone

import pytest

from models.lab_insights import LabAnalysisSnapshot
from models.nutrition import NutritionDayStatus, NutritionLog
from models.program import Program, ProgramDay
from models.schedule import ScheduledWorkout
from models.user import User
from models.workout import Workout
from routers.ai import get_lab_insights_v2, record_lab_insight_impressions
from routers.account import _delete_user_owned_records
from routers.nutrition import (
    get_date_summary,
    get_nutrition_targets,
    set_nutrition_day_completion,
    update_nutrition_targets,
)
from routers.workouts import create_workout, update_workout
from schemas.lab_insights_v2 import InsightImpressions, LabInsightsV2Response
from schemas.nutrition import NutritionDayCompletionUpdate, NutritionTargetsUpdate
from schemas.workout import WorkoutCreate, WorkoutUpdate
from services.lab_insights_v2 import LabInsightsV2Engine
from tests.support import TestingSessionLocal


def _user(db, email: str = "lab-route@example.com") -> User:
    user = User(
        email=email,
        hashed_password="not-used",
        full_name="Lab Route Tester",
        is_verified=True,
        timezone="UTC",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_v2_contract_parses_and_records_privacy_safe_impressions():
    db = TestingSessionLocal()
    user = _user(db)
    payload = get_lab_insights_v2(refresh=False, current_user=user, db=db)
    contract = LabInsightsV2Response.model_validate(payload)

    assert contract.schema_version == "2.0"
    assert contract.generated_at
    assert contract.data_through
    assert contract.stale_after
    assert contract.source_data_watermark
    assert isinstance(contract.metrics, dict)
    assert isinstance(contract.domain_coverage, list)

    ids = [item["id"] for item in contract.insights]
    result = record_lab_insight_impressions(
        data=InsightImpressions(insight_ids=ids), current_user=user, db=db
    )
    assert result["recorded"] == len(ids)
    db.close()


def test_nutrition_completion_and_targets_are_explicit_and_persisted():
    db = TestingSessionLocal()
    user = _user(db, "nutrition-route@example.com")
    target = date.today()

    with pytest.raises(Exception) as exc:
        set_nutrition_day_completion(
            target_date=target,
            data=NutritionDayCompletionUpdate(is_complete=True),
            current_user=user,
            db=db,
        )
    assert getattr(exc.value, "status_code", None) == 422

    db.add(
        NutritionLog(
            user_id=user.id,
            date=target,
            meal_name="Snack",
            food_name="Yogurt",
            calories=100,
            protein_g=10,
        )
    )
    db.commit()
    completed = set_nutrition_day_completion(
        target_date=target,
        data=NutritionDayCompletionUpdate(is_complete=True),
        current_user=user,
        db=db,
    )
    assert completed.is_complete is True
    summary = get_date_summary(target_date=target, current_user=user, db=db)
    assert summary["is_complete"] is True

    update = NutritionTargetsUpdate(
        calorie_target=2400,
        protein_target_g=160,
        carbs_target_g=250,
        fat_target_g=70,
    )
    stored = update_nutrition_targets(data=update, current_user=user, db=db)
    assert stored["configured"] is True
    assert get_nutrition_targets(current_user=user)["protein_target_g"] == 160
    assert db.query(NutritionDayStatus).filter_by(user_id=user.id).one().is_complete
    assert db.query(User).filter_by(id=user.id).one().protein_target_g == 160
    db.close()


def test_workout_linkage_rejects_cross_user_and_marks_owned_schedule_complete():
    db = TestingSessionLocal()
    user = _user(db, "link-owner@example.com")
    other = _user(db, "link-other@example.com")
    program = Program(user_id=user.id, name="Owned", is_active=True, days_per_week=1)
    other_program = Program(user_id=other.id, name="Other", is_active=True, days_per_week=1)
    db.add_all([program, other_program])
    db.flush()
    day = ProgramDay(program_id=program.id, day_number=1, day_name="Owned day")
    other_day = ProgramDay(program_id=other_program.id, day_number=1, day_name="Other day")
    db.add_all([day, other_day])
    db.flush()
    schedule = ScheduledWorkout(
        user_id=user.id,
        program_id=program.id,
        program_day_id=day.id,
        scheduled_date=date.today(),
        status="planned",
        linkage_trustworthy=True,
    )
    other_schedule = ScheduledWorkout(
        user_id=other.id,
        program_id=other_program.id,
        program_day_id=other_day.id,
        scheduled_date=date.today(),
        status="planned",
        linkage_trustworthy=True,
    )
    db.add_all([schedule, other_schedule])
    db.commit()

    with pytest.raises(Exception) as exc:
        create_workout(
            data=WorkoutCreate(scheduled_workout_id=other_schedule.id),
            current_user=user,
            db=db,
        )
    assert getattr(exc.value, "status_code", None) == 422

    created = create_workout(
        data=WorkoutCreate(scheduled_workout_id=schedule.id),
        current_user=user,
        db=db,
    )
    assert created["program_id"] == program.id
    finalized = update_workout(
        workout_id=created["id"],
        data=WorkoutUpdate(completed=True),
        current_user=user,
        db=db,
    )
    assert finalized["completed_at"] is not None
    db.refresh(schedule)
    assert schedule.status == "completed"
    assert schedule.completed_at is not None
    db.close()


def test_refresh_failure_returns_old_snapshot_as_visibly_stale(monkeypatch):
    db = TestingSessionLocal()
    user = _user(db, "stale@example.com")
    first = get_lab_insights_v2(refresh=False, current_user=user, db=db)

    def fail_generate(self, *, force_refresh=False):
        raise RuntimeError("forced generation failure")

    monkeypatch.setattr(LabInsightsV2Engine, "generate", fail_generate)
    fallback = get_lab_insights_v2(refresh=True, current_user=user, db=db)
    assert fallback["analysis_id"] == first["analysis_id"]
    assert fallback["is_stale"] is True
    assert fallback["cache_status"] == "stale_fallback"
    assert db.query(LabAnalysisSnapshot).filter_by(user_id=user.id).count() == 1
    db.close()


def test_account_deletion_handles_new_program_and_schedule_links_atomically():
    db = TestingSessionLocal()
    user = _user(db, "linked-delete@example.com")
    program = Program(user_id=user.id, name="Linked", is_active=True, days_per_week=1)
    db.add(program)
    db.flush()
    day = ProgramDay(program_id=program.id, day_number=1, day_name="Day")
    db.add(day)
    db.flush()
    schedule = ScheduledWorkout(
        user_id=user.id,
        program_id=program.id,
        program_day_id=day.id,
        scheduled_date=date.today(),
        status="completed",
        linkage_trustworthy=True,
    )
    db.add(schedule)
    db.flush()
    db.add(
        Workout(
            user_id=user.id,
            date=date.today(),
            completed_at=datetime.now(timezone.utc),
            program_id=program.id,
            program_day_id=day.id,
            scheduled_workout_id=schedule.id,
        )
    )
    db.commit()

    _delete_user_owned_records(db, user)
    db.commit()

    assert db.query(User).filter_by(email="linked-delete@example.com").first() is None
    assert db.query(Workout).count() == 0
    assert db.query(ScheduledWorkout).count() == 0
    assert db.query(Program).count() == 0
    db.close()
