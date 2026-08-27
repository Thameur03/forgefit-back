"""Integration coverage for transactional self-service account deletion."""

from datetime import date, datetime, timedelta, timezone

from auth.utils import create_access_token, hash_password
from models.account_deletion import AccountDeletionChallenge
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.program import Program, ProgramDay, ProgramExercise
from models.schedule import ScheduledWorkout
from models.token import RevokedToken
from models.user import User
from models.workout import Workout, WorkoutSet
from routers import account as account_router
from tests.support import TestingSessionLocal, client


def _seed_user_graph(email: str, suffix: str) -> tuple[int, str]:
    db = TestingSessionLocal()
    user = User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name=f"User {suffix}",
        is_verified=True,
    )
    db.add(user)
    db.flush()

    workout = Workout(user_id=user.id, date=date(2026, 8, 18), name="Session")
    workout.sets.append(
        WorkoutSet(
            exercise_name="Squat",
            sets=3,
            reps=5,
            weight_kg=100,
        )
    )
    db.add(workout)

    db.add(
        NutritionLog(
            user_id=user.id,
            date=date(2026, 8, 18),
            meal_name="Lunch",
            food_name="Rice",
            calories=300,
        )
    )

    program = Program(user_id=user.id, name="Strength")
    day = ProgramDay(day_number=1, day_name="Day One")
    day.exercises.append(
        ProgramExercise(
            exercise_name="Squat",
            sets=3,
            reps=5,
            order_index=0,
        )
    )
    program.days.append(day)
    db.add(program)
    db.flush()

    db.add(
        ScheduledWorkout(
            user_id=user.id,
            program_id=program.id,
            program_day_id=day.id,
            scheduled_date=date(2026, 8, 19),
        )
    )
    db.add(RevokedToken(token_jti=f"old-token-{suffix}", user_id=user.id))
    db.add(
        AccountDeletionChallenge(
            user_id=user.id,
            code_hash=hash_password("123456"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
    )
    db.add(
        AnalyticsEvent(
            user_id=user.id,
            event_name="test_event",
            properties={"source": "account-deletion-test"},
        )
    )
    db.commit()

    user_id = user.id
    token = create_access_token({"sub": user.email})
    db.close()
    return user_id, token


def _counts(user_id: int) -> dict[str, int]:
    db = TestingSessionLocal()
    program_ids = [
        row.id for row in db.query(Program.id).filter(Program.user_id == user_id)
    ]
    workout_ids = [
        row.id for row in db.query(Workout.id).filter(Workout.user_id == user_id)
    ]
    day_ids = [
        row.id
        for row in db.query(ProgramDay.id).filter(
            ProgramDay.program_id.in_(program_ids)
        )
    ] if program_ids else []
    result = {
        "users": db.query(User).filter(User.id == user_id).count(),
        "workouts": len(workout_ids),
        "workout_sets": db.query(WorkoutSet).filter(
            WorkoutSet.workout_id.in_(workout_ids)
        ).count() if workout_ids else 0,
        "nutrition_logs": db.query(NutritionLog).filter(
            NutritionLog.user_id == user_id
        ).count(),
        "programs": len(program_ids),
        "program_days": len(day_ids),
        "program_exercises": db.query(ProgramExercise).filter(
            ProgramExercise.program_day_id.in_(day_ids)
        ).count() if day_ids else 0,
        "scheduled_workouts": db.query(ScheduledWorkout).filter(
            ScheduledWorkout.user_id == user_id
        ).count(),
        "revoked_tokens": db.query(RevokedToken).filter(
            RevokedToken.user_id == user_id
        ).count(),
        "deletion_challenges": db.query(AccountDeletionChallenge).filter(
            AccountDeletionChallenge.user_id == user_id
        ).count(),
        "analytics_events": db.query(AnalyticsEvent).filter(
            AnalyticsEvent.user_id == user_id
        ).count(),
    }
    db.close()
    return result


def _total_counts() -> dict[str, int]:
    db = TestingSessionLocal()
    result = {
        "users": db.query(User).count(),
        "workouts": db.query(Workout).count(),
        "workout_sets": db.query(WorkoutSet).count(),
        "nutrition_logs": db.query(NutritionLog).count(),
        "programs": db.query(Program).count(),
        "program_days": db.query(ProgramDay).count(),
        "program_exercises": db.query(ProgramExercise).count(),
        "scheduled_workouts": db.query(ScheduledWorkout).count(),
        "revoked_tokens": db.query(RevokedToken).count(),
        "deletion_challenges": db.query(AccountDeletionChallenge).count(),
        "analytics_events": db.query(AnalyticsEvent).count(),
    }
    db.close()
    return result


def test_delete_account_removes_complete_graph_and_preserves_other_user():
    target_id, target_token = _seed_user_graph("delete@example.com", "target")
    other_id, _ = _seed_user_graph("keep@example.com", "other")

    response = client.delete(
        "/account/me",
        headers={"Authorization": f"Bearer {target_token}"},
    )

    assert response.status_code == 200, response.text
    assert all(count == 0 for count in _counts(target_id).values())
    assert all(count == 1 for count in _counts(other_id).values())
    assert all(count == 1 for count in _total_counts().values())


def test_delete_account_requires_authentication():
    response = client.delete("/account/me")
    assert response.status_code in (401, 403)


def test_delete_account_rolls_back_every_change_on_failure(monkeypatch):
    target_id, target_token = _seed_user_graph("rollback@example.com", "rollback")
    original = account_router._delete_user_owned_records

    def delete_then_fail(db, user):
        original(db, user)
        raise RuntimeError("simulated failure after staged deletes")

    monkeypatch.setattr(
        account_router,
        "_delete_user_owned_records",
        delete_then_fail,
    )

    response = client.delete(
        "/account/me",
        headers={"Authorization": f"Bearer {target_token}"},
    )

    assert response.status_code == 500
    assert all(count == 1 for count in _counts(target_id).values())
