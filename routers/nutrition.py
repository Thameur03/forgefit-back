from datetime import date
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models.user import User
from models.nutrition import NutritionLog
from schemas.nutrition import (
    NutritionLogCreate,
    NutritionLogResponse,
    DailySummary,
)
from auth.utils import get_current_user

router = APIRouter()


def _build_daily_summary(target_date: date, logs: list[NutritionLog]) -> dict:
    """Build a DailySummary dict from a list of NutritionLog objects."""
    total_calories = sum(log.calories for log in logs)
    total_protein = sum(log.protein_g or 0.0 for log in logs)
    total_carbs = sum(log.carbs_g or 0.0 for log in logs)
    total_fat = sum(log.fat_g or 0.0 for log in logs)

    log_responses = [
        NutritionLogResponse.model_validate(log) for log in logs
    ]

    meals: dict[str, list] = defaultdict(list)
    for lr in log_responses:
        meals[lr.meal_name].append(lr)

    return {
        "date": target_date,
        "total_calories": total_calories,
        "total_protein_g": total_protein,
        "total_carbs_g": total_carbs,
        "total_fat_g": total_fat,
        "logs": log_responses,
        "meals": dict(meals),
    }


@router.post("/", response_model=NutritionLogResponse, status_code=status.HTTP_201_CREATED)
def create_nutrition_log(
    data: NutritionLogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a food entry for the current user.

    Accepts meal name, food name, calories, and optional macros.
    Defaults to today's date if not specified.
    """
    log_date = data.date if data.date is not None else date.today()
    log = NutritionLog(
        user_id=current_user.id,
        date=log_date,
        meal_name=data.meal_name.value,
        food_name=data.food_name,
        calories=data.calories,
        protein_g=data.protein_g,
        carbs_g=data.carbs_g,
        fat_g=data.fat_g,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/today", response_model=DailySummary, status_code=status.HTTP_200_OK)
def get_today_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the daily nutrition summary for today.

    Returns total calories, macros, all logs, and logs grouped by meal.
    Returns an empty summary with zeros if no logs exist for today.
    """
    today = date.today()
    logs = (
        db.query(NutritionLog)
        .filter(NutritionLog.user_id == current_user.id, NutritionLog.date == today)
        .order_by(NutritionLog.id)
        .all()
    )
    return _build_daily_summary(today, logs)


@router.get("/history", response_model=list[DailySummary], status_code=status.HTTP_200_OK)
def get_nutrition_history(
    limit: int = Query(30, ge=1, le=90, description="Number of days to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get nutrition history as daily summaries ordered by date descending.

    Returns one summary per day that has at least one log entry.
    Useful for weekly or monthly nutrition overviews.
    """
    # Get distinct dates that have logs, ordered descending
    date_rows = (
        db.query(NutritionLog.date)
        .filter(NutritionLog.user_id == current_user.id)
        .group_by(NutritionLog.date)
        .order_by(NutritionLog.date.desc())
        .limit(limit)
        .all()
    )
    dates = [row[0] for row in date_rows]

    if not dates:
        return []

    # Fetch all logs for those dates in one query
    all_logs = (
        db.query(NutritionLog)
        .filter(
            NutritionLog.user_id == current_user.id,
            NutritionLog.date.in_(dates),
        )
        .order_by(NutritionLog.date.desc(), NutritionLog.id)
        .all()
    )

    # Group logs by date
    logs_by_date: dict[date, list] = defaultdict(list)
    for log in all_logs:
        logs_by_date[log.date].append(log)

    return [_build_daily_summary(d, logs_by_date[d]) for d in dates]


@router.get("/date/{target_date}", response_model=DailySummary, status_code=status.HTTP_200_OK)
def get_date_summary(
    target_date: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the daily nutrition summary for a specific date.

    Date format: YYYY-MM-DD. Returns an empty summary with zeros
    if no logs exist for the given date.
    """
    logs = (
        db.query(NutritionLog)
        .filter(NutritionLog.user_id == current_user.id, NutritionLog.date == target_date)
        .order_by(NutritionLog.id)
        .all()
    )
    return _build_daily_summary(target_date, logs)


@router.put("/{log_id}", response_model=NutritionLogResponse, status_code=status.HTTP_200_OK)
def update_nutrition_log(
    log_id: int,
    data: NutritionLogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an existing food log entry.

    Validates that the log belongs to the current user.
    Replaces all fields with the new values.
    """
    log = db.query(NutritionLog).filter(NutritionLog.id == log_id).first()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Food log not found"
        )
    if log.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this food log",
        )
    log.date = data.date if data.date is not None else log.date
    log.meal_name = data.meal_name.value
    log.food_name = data.food_name
    log.calories = data.calories
    log.protein_g = data.protein_g
    log.carbs_g = data.carbs_g
    log.fat_g = data.fat_g
    db.commit()
    db.refresh(log)
    return log


@router.delete("/{log_id}", status_code=status.HTTP_200_OK)
def delete_nutrition_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single food log entry.

    Validates that the log belongs to the current user.
    Returns a confirmation message.
    """
    log = db.query(NutritionLog).filter(NutritionLog.id == log_id).first()
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Food log not found"
        )
    if log.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this food log",
        )
    db.delete(log)
    db.commit()
    return {"message": "Food log deleted"}
