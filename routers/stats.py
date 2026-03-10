from datetime import date, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models.user import User
from models.workout import Workout, WorkoutSet
from models.nutrition import NutritionLog
from schemas.stats import (
    WorkoutStats,
    NutritionStats,
    PersonalRecord,
    WeeklyWorkoutData,
    DailyNutritionData,
)
from auth.utils import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_streaks(workout_dates: list[date]) -> tuple[int, int]:
    """Compute current and longest consecutive-day streaks.

    Args:
        workout_dates: distinct workout dates sorted ascending.

    Returns:
        (current_streak_days, longest_streak_days)
    """
    if not workout_dates:
        return 0, 0

    unique = sorted(set(workout_dates))
    today = date.today()

    # Build all streaks
    longest = 1
    current_run = 1
    for i in range(1, len(unique)):
        if (unique[i] - unique[i - 1]).days == 1:
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 1
    longest = max(longest, current_run)

    # Current streak: must include today or yesterday
    current_streak = 0
    if unique[-1] == today or unique[-1] == today - timedelta(days=1):
        current_streak = 1
        for i in range(len(unique) - 2, -1, -1):
            if (unique[i + 1] - unique[i]).days == 1:
                current_streak += 1
            else:
                break

    return current_streak, longest


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/workouts", response_model=WorkoutStats, status_code=status.HTTP_200_OK)
def get_workout_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overall workout statistics for the current user.

    Returns totals, averages, streaks, and most frequent exercise.
    All values default to zero if the user has no workout data.
    """
    # Total workouts
    total_workouts = (
        db.query(func.count(Workout.id))
        .filter(Workout.user_id == current_user.id)
        .scalar()
    ) or 0

    if total_workouts == 0:
        return WorkoutStats()

    # Total sets
    total_sets = (
        db.query(func.count(WorkoutSet.id))
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(Workout.user_id == current_user.id)
        .scalar()
    ) or 0

    # Total volume
    total_volume = (
        db.query(func.sum(WorkoutSet.sets * WorkoutSet.reps * WorkoutSet.weight_kg))
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(Workout.user_id == current_user.id, WorkoutSet.weight_kg.isnot(None))
        .scalar()
    ) or 0.0

    # Avg workouts per week
    first_workout_date = (
        db.query(func.min(Workout.date))
        .filter(Workout.user_id == current_user.id)
        .scalar()
    )
    today = date.today()
    weeks_since_first = max(((today - first_workout_date).days / 7), 1) if first_workout_date else 1
    avg_per_week = round(total_workouts / weeks_since_first, 2)

    # Most frequent exercise
    most_frequent_row = (
        db.query(WorkoutSet.exercise_name, func.count(WorkoutSet.id).label("cnt"))
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(Workout.user_id == current_user.id)
        .group_by(WorkoutSet.exercise_name)
        .order_by(func.count(WorkoutSet.id).desc())
        .first()
    )
    most_frequent_exercise = most_frequent_row[0] if most_frequent_row else None

    # Streaks
    workout_date_rows = (
        db.query(Workout.date)
        .filter(Workout.user_id == current_user.id)
        .distinct()
        .order_by(Workout.date.asc())
        .all()
    )
    workout_dates = [row[0] for row in workout_date_rows]
    current_streak, longest_streak = _compute_streaks(workout_dates)

    return WorkoutStats(
        total_workouts=total_workouts,
        total_sets=total_sets,
        total_volume_kg=round(float(total_volume), 2),
        avg_workouts_per_week=avg_per_week,
        most_trained_muscle=None,  # would require muscle mapping from exercises
        most_frequent_exercise=most_frequent_exercise,
        current_streak_days=current_streak,
        longest_streak_days=longest_streak,
    )


@router.get("/nutrition", response_model=NutritionStats, status_code=status.HTTP_200_OK)
def get_nutrition_stats(
    days: int = Query(30, ge=1, le=90, description="Number of days to analyze"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get nutrition statistics over the last N days.

    Averages are calculated over days that have logs, not total days.
    Returns zeros if the user has no nutrition logs in the period.
    """
    today = date.today()
    start_date = today - timedelta(days=days)

    # Daily aggregates
    daily_rows = (
        db.query(
            NutritionLog.date,
            func.sum(NutritionLog.calories).label("total_cal"),
            func.sum(NutritionLog.protein_g).label("total_pro"),
            func.sum(NutritionLog.carbs_g).label("total_carbs"),
            func.sum(NutritionLog.fat_g).label("total_fat"),
        )
        .filter(
            NutritionLog.user_id == current_user.id,
            NutritionLog.date > start_date,
        )
        .group_by(NutritionLog.date)
        .all()
    )

    if not daily_rows:
        return NutritionStats()

    days_logged = len(daily_rows)
    total_cal = sum(float(r.total_cal or 0) for r in daily_rows)
    total_pro = sum(float(r.total_pro or 0) for r in daily_rows)
    total_carbs = sum(float(r.total_carbs or 0) for r in daily_rows)
    total_fat = sum(float(r.total_fat or 0) for r in daily_rows)
    best_day = max(float(r.total_cal or 0) for r in daily_rows)

    return NutritionStats(
        avg_daily_calories=round(total_cal / days_logged, 2),
        avg_daily_protein_g=round(total_pro / days_logged, 2),
        avg_daily_carbs_g=round(total_carbs / days_logged, 2),
        avg_daily_fat_g=round(total_fat / days_logged, 2),
        days_logged=days_logged,
        best_day_calories=round(best_day, 2),
    )


@router.get(
    "/personal-records",
    response_model=list[PersonalRecord],
    status_code=status.HTTP_200_OK,
)
def get_personal_records(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get personal records for each exercise the user has logged.

    Returns the heaviest weight and highest reps achieved per exercise,
    ordered by max weight descending. Only includes exercises with weight data.
    """
    # For each exercise, find the max weight
    subq = (
        db.query(
            WorkoutSet.exercise_name,
            func.max(WorkoutSet.weight_kg).label("max_weight"),
            func.max(WorkoutSet.reps).label("max_reps"),
        )
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(
            Workout.user_id == current_user.id,
            WorkoutSet.weight_kg.isnot(None),
        )
        .group_by(WorkoutSet.exercise_name)
        .order_by(func.max(WorkoutSet.weight_kg).desc())
        .all()
    )

    records = []
    for row in subq:
        # Find the date when max weight was achieved
        date_row = (
            db.query(Workout.date)
            .join(WorkoutSet, WorkoutSet.workout_id == Workout.id)
            .filter(
                Workout.user_id == current_user.id,
                WorkoutSet.exercise_name == row.exercise_name,
                WorkoutSet.weight_kg == row.max_weight,
            )
            .order_by(Workout.date.desc())
            .first()
        )
        records.append(
            PersonalRecord(
                exercise_name=row.exercise_name,
                max_weight_kg=round(float(row.max_weight), 2),
                max_reps=row.max_reps,
                date_achieved=date_row[0] if date_row else date.today(),
            )
        )

    return records


@router.get(
    "/weekly-volume",
    response_model=list[WeeklyWorkoutData],
    status_code=status.HTTP_200_OK,
)
def get_weekly_volume(
    weeks: int = Query(8, ge=1, le=24, description="Number of weeks"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get weekly workout volume for the last N weeks.

    Returns exactly {weeks} entries, including weeks with zero workouts.
    week_start is always a Monday. Ordered oldest-first for chart rendering.
    """
    today = date.today()
    # Monday of current week
    current_monday = today - timedelta(days=today.weekday())
    start_monday = current_monday - timedelta(weeks=weeks - 1)

    # Build all week start dates
    all_weeks = [start_monday + timedelta(weeks=i) for i in range(weeks)]
    end_date = current_monday + timedelta(days=7)

    # Fetch workouts in the range
    workouts = (
        db.query(Workout)
        .filter(
            Workout.user_id == current_user.id,
            Workout.date >= start_monday,
            Workout.date < end_date,
        )
        .all()
    )

    # Fetch sets for those workouts
    workout_ids = [w.id for w in workouts]
    sets_data = []
    if workout_ids:
        sets_data = (
            db.query(WorkoutSet)
            .filter(WorkoutSet.workout_id.in_(workout_ids))
            .all()
        )

    # Map workout_id to date
    workout_date_map = {w.id: w.date for w in workouts}

    # Group data by week
    week_data: dict[date, dict] = {
        ws: {"count": 0, "volume": 0.0} for ws in all_weeks
    }

    # Count workouts per week
    for w in workouts:
        w_monday = w.date - timedelta(days=w.date.weekday())
        if w_monday in week_data:
            week_data[w_monday]["count"] += 1

    # Sum volume per week
    for s in sets_data:
        w_date = workout_date_map.get(s.workout_id)
        if w_date:
            w_monday = w_date - timedelta(days=w_date.weekday())
            if w_monday in week_data and s.weight_kg is not None:
                week_data[w_monday]["volume"] += s.sets * s.reps * s.weight_kg

    return [
        WeeklyWorkoutData(
            week_start=ws,
            workout_count=week_data[ws]["count"],
            total_volume_kg=round(week_data[ws]["volume"], 2),
        )
        for ws in all_weeks
    ]


@router.get(
    "/nutrition-trend",
    response_model=list[DailyNutritionData],
    status_code=status.HTTP_200_OK,
)
def get_nutrition_trend(
    days: int = Query(30, ge=1, le=90, description="Number of days"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get daily nutrition data for the last N days.

    Returns one entry per day that has nutrition logs, ordered oldest-first.
    Powers the nutrition trend chart in the app.
    """
    today = date.today()
    start_date = today - timedelta(days=days)

    daily_rows = (
        db.query(
            NutritionLog.date,
            func.sum(NutritionLog.calories).label("total_cal"),
            func.sum(NutritionLog.protein_g).label("total_pro"),
            func.sum(NutritionLog.carbs_g).label("total_carbs"),
            func.sum(NutritionLog.fat_g).label("total_fat"),
        )
        .filter(
            NutritionLog.user_id == current_user.id,
            NutritionLog.date > start_date,
        )
        .group_by(NutritionLog.date)
        .order_by(NutritionLog.date.asc())
        .all()
    )

    return [
        DailyNutritionData(
            date=row.date,
            calories=round(float(row.total_cal or 0), 2),
            protein_g=round(float(row.total_pro or 0), 2),
            carbs_g=round(float(row.total_carbs or 0), 2),
            fat_g=round(float(row.total_fat or 0), 2),
        )
        for row in daily_rows
    ]
