"""Focused correctness coverage for history lookup used by Finish Workout."""

from datetime import date, datetime, timezone

from sqlalchemy import event

from models.user import User
from models.workout import Workout, WorkoutSet
from routers.workouts import _get_batch_last_sessions
from tests.support import TestingSessionLocal, test_engine


def test_batch_last_sessions_ranks_in_database_without_changing_selection():
    db = TestingSessionLocal()
    user = User(
        email="finish-history@example.com",
        hashed_password="unused",
        full_name="Finish History",
        is_verified=True,
    )
    db.add(user)
    db.flush()
    user_id = user.id

    older = Workout(
        user_id=user_id,
        date=date(2026, 8, 1),
        completed_at=datetime.now(timezone.utc),
    )
    latest = Workout(
        user_id=user_id,
        date=date(2026, 8, 2),
        completed_at=datetime.now(timezone.utc),
    )
    incomplete = Workout(user_id=user_id, date=date(2026, 8, 3))
    same_day = Workout(
        user_id=user_id,
        date=date(2026, 8, 4),
        completed_at=datetime.now(timezone.utc),
    )
    db.add_all([older, latest, incomplete, same_day])
    db.flush()
    db.add_all(
        [
            WorkoutSet(
                workout_id=older.id,
                exercise_name="Bench Press",
                sets=1,
                reps=5,
                weight_kg=80,
            ),
            WorkoutSet(
                workout_id=latest.id,
                exercise_name="bench press",
                sets=1,
                reps=6,
                weight_kg=85,
            ),
            # Same workout date: the higher set ID is the existing tie-breaker.
            WorkoutSet(
                workout_id=latest.id,
                exercise_name="BENCH PRESS",
                sets=1,
                reps=7,
                weight_kg=90,
            ),
            WorkoutSet(
                workout_id=incomplete.id,
                exercise_name="Bench Press",
                sets=1,
                reps=8,
                weight_kg=95,
            ),
            WorkoutSet(
                workout_id=same_day.id,
                exercise_name="Bench Press",
                sets=1,
                reps=9,
                weight_kg=100,
            ),
        ]
    )
    db.commit()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture_statement)
    try:
        result = _get_batch_last_sessions(
            db,
            user_id,
            ["Bench Press"],
            before_date=date(2026, 8, 4),
        )
    finally:
        event.remove(test_engine, "before_cursor_execute", capture_statement)
        db.close()

    assert list(result) == ["bench press"]
    assert result["bench press"].date == date(2026, 8, 2)
    assert result["bench press"].reps == 7
    assert result["bench press"].weight_kg == 90
    assert len(statements) == 1
    assert "row_number() OVER" in statements[0]
