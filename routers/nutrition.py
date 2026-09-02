from datetime import date, datetime, timezone
from collections import defaultdict
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models.user import User
from models.nutrition import NutritionDayStatus, NutritionLog
from schemas.nutrition import (
    NutritionLogCreate,
    NutritionLogResponse,
    DailySummary,
    NutritionDayCompletionResponse,
    NutritionDayCompletionUpdate,
    NutritionTargetsResponse,
    NutritionTargetsUpdate,
)
from auth.utils import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_daily_summary(
    target_date: date,
    logs: list[NutritionLog],
    day_status: NutritionDayStatus | None = None,
) -> dict:
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
        "is_complete": bool(day_status and day_status.is_complete and logs),
        "completed_at": day_status.completed_at if day_status and logs else None,
    }


def _day_status(db: Session, user_id: int, target_date: date) -> NutritionDayStatus | None:
    return (
        db.query(NutritionDayStatus)
        .filter(
            NutritionDayStatus.user_id == user_id,
            NutritionDayStatus.date == target_date,
        )
        .first()
    )


@router.post("/", response_model=NutritionLogResponse, status_code=status.HTTP_201_CREATED)
def create_nutrition_log(
    data: NutritionLogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a food entry for the current user.

    Accepts meal name, food name, calories, and optional macros.
    Defaults to today's date if not specified.

    When client_request_id is provided the endpoint is idempotent:
    - A second request with the same (user_id, client_request_id) returns
      HTTP 200 with the existing row — no duplicate is created.
    - Requests without client_request_id use legacy behaviour (always insert).

    NOTE: this endpoint saves exactly ONE nutrition-log row per call.
    There is no atomic multi-food meal transaction endpoint.
    """
    # ── Idempotency pre-check ─────────────────────────────────────────────────
    if data.client_request_id:
        existing = (
            db.query(NutritionLog)
            .filter(
                NutritionLog.user_id == current_user.id,
                NutritionLog.client_request_id == data.client_request_id,
            )
            .first()
        )
        if existing:
            logger.info(
                "[Nutrition] idempotent replay returned existing log id=%s",
                existing.id,
            )
            from fastapi.responses import JSONResponse
            from fastapi.encoders import jsonable_encoder
            response_data = NutritionLogResponse.model_validate(existing)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(response_data),
            )

    log_date = data.date if data.date is not None else date.today()
    log = NutritionLog(
        user_id=current_user.id,
        date=log_date,
        meal_name=data.meal_name,
        food_name=data.food_name,
        calories=data.calories,
        protein_g=data.protein_g,
        carbs_g=data.carbs_g,
        fat_g=data.fat_g,
        fdc_id=data.fdc_id,
        client_request_id=data.client_request_id,
    )
    db.add(log)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # Race-condition recovery: another request with the same key committed
        # first (IntegrityError from the partial unique index).
        if data.client_request_id:
            existing = (
                db.query(NutritionLog)
                .filter(
                    NutritionLog.user_id == current_user.id,
                    NutritionLog.client_request_id == data.client_request_id,
                )
                .first()
            )
            if existing:
                logger.info(
                    "[Nutrition] race recovery: returning existing log id=%s",
                    existing.id,
                )
                from fastapi.responses import JSONResponse
                from fastapi.encoders import jsonable_encoder
                response_data = NutritionLogResponse.model_validate(existing)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content=jsonable_encoder(response_data),
                )
        raise exc

    db.refresh(log)
    logger.info("[Nutrition] saved log id=%s", log.id)
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
    total_calories = sum(log.calories for log in logs)
    return _build_daily_summary(today, logs, _day_status(db, current_user.id, today))


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

    statuses = {
        item.date: item
        for item in db.query(NutritionDayStatus)
        .filter(
            NutritionDayStatus.user_id == current_user.id,
            NutritionDayStatus.date.in_(dates),
        )
        .all()
    }
    return [_build_daily_summary(d, logs_by_date[d], statuses.get(d)) for d in dates]


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
    return _build_daily_summary(
        target_date, logs, _day_status(db, current_user.id, target_date)
    )


@router.get("/targets", response_model=NutritionTargetsResponse)
def get_nutrition_targets(
    current_user: User = Depends(get_current_user),
):
    values = {
        "calorie_target": current_user.calorie_target,
        "protein_target_g": current_user.protein_target_g,
        "carbs_target_g": current_user.carbs_target_g,
        "fat_target_g": current_user.fat_target_g,
    }
    return {**values, "configured": any(value is not None for value in values.values())}


@router.put("/targets", response_model=NutritionTargetsResponse)
def update_nutrition_targets(
    data: NutritionTargetsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(current_user, key, value)
    db.commit()
    db.refresh(current_user)
    values = {
        "calorie_target": current_user.calorie_target,
        "protein_target_g": current_user.protein_target_g,
        "carbs_target_g": current_user.carbs_target_g,
        "fat_target_g": current_user.fat_target_g,
    }
    return {**values, "configured": any(value is not None for value in values.values())}


@router.put(
    "/days/{target_date}/completion",
    response_model=NutritionDayCompletionResponse,
)
def set_nutrition_day_completion(
    target_date: date,
    data: NutritionDayCompletionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if target_date > date.today():
        raise HTTPException(status_code=422, detail="A future day cannot be completed")
    has_logs = (
        db.query(NutritionLog.id)
        .filter(
            NutritionLog.user_id == current_user.id,
            NutritionLog.date == target_date,
        )
        .first()
        is not None
    )
    if data.is_complete and not has_logs:
        raise HTTPException(
            status_code=422,
            detail="A nutrition day needs at least one log before it can be completed",
        )
    row = _day_status(db, current_user.id, target_date)
    if row is None:
        row = NutritionDayStatus(user_id=current_user.id, date=target_date)
        db.add(row)
    now = datetime.now(timezone.utc)
    row.is_complete = data.is_complete
    row.completed_at = now if data.is_complete else None
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


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
    log.meal_name = data.meal_name
    log.food_name = data.food_name
    log.calories = data.calories
    log.protein_g = data.protein_g
    log.carbs_g = data.carbs_g
    log.fat_g = data.fat_g
    log.fdc_id = data.fdc_id
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
