"""Privacy-conscious user operations and User 360 query helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, exists, func, not_, or_
from sqlalchemy.orm import Session

from models.account_deletion import AccountDeletionChallenge
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.program import Program
from models.schedule import ScheduledWorkout
from models.token import RevokedToken
from models.user import User
from models.workout import Workout, WorkoutSet
from services.analytics_events import (
    MEANINGFUL_ACTIVITY_EVENTS,
    safe_stored_event_properties,
)


UTC = timezone.utc


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _activity_condition(
    *,
    after: datetime | None = None,
    before: datetime | None = None,
):
    workout_filters = [
        Workout.user_id == User.id,
        Workout.completed_at.is_not(None),
    ]
    nutrition_filters = [NutritionLog.user_id == User.id]
    event_filters = [
        AnalyticsEvent.user_id == User.id,
        AnalyticsEvent.event_name.in_(MEANINGFUL_ACTIVITY_EVENTS),
    ]
    if after is not None:
        workout_filters.append(Workout.date >= after.date())
        nutrition_filters.append(NutritionLog.date >= after.date())
        event_filters.append(AnalyticsEvent.occurred_at >= after)
    if before is not None:
        workout_filters.append(Workout.date < before.date())
        nutrition_filters.append(NutritionLog.date < before.date())
        event_filters.append(AnalyticsEvent.occurred_at < before)
    return or_(
        exists().where(*workout_filters),
        exists().where(*nutrition_filters),
        exists().where(*event_filters),
    )


def _last_activity_map(db: Session, user_ids: list[int]) -> dict[int, datetime]:
    if not user_ids:
        return {}
    result: dict[int, datetime] = {}
    workout_rows = (
        db.query(Workout.user_id, func.max(Workout.date).label("latest"))
        .filter(
            Workout.user_id.in_(user_ids),
            Workout.completed_at.is_not(None),
        )
        .group_by(Workout.user_id)
        .all()
    )
    nutrition_rows = (
        db.query(NutritionLog.user_id, func.max(NutritionLog.date).label("latest"))
        .filter(NutritionLog.user_id.in_(user_ids))
        .group_by(NutritionLog.user_id)
        .all()
    )
    event_rows = (
        db.query(
            AnalyticsEvent.user_id,
            func.max(AnalyticsEvent.occurred_at).label("latest"),
        )
        .filter(
            AnalyticsEvent.user_id.in_(user_ids),
            AnalyticsEvent.event_name.in_(MEANINGFUL_ACTIVITY_EVENTS),
        )
        .group_by(AnalyticsEvent.user_id)
        .all()
    )
    for rows, date_only in ((workout_rows, True), (nutrition_rows, True), (event_rows, False)):
        for row in rows:
            if row.latest is None:
                continue
            candidate = (
                datetime.combine(row.latest, time.min, tzinfo=UTC)
                if date_only
                else _utc(row.latest)
            )
            if row.user_id not in result or candidate > result[row.user_id]:
                result[row.user_id] = candidate
    return result


def activity_state(last_active: datetime | None, *, today: date | None = None) -> str:
    if last_active is None:
        return "never_active"
    reference = today or datetime.now(UTC).date()
    inactivity = (reference - _utc(last_active).date()).days
    if inactivity <= 7:
        return "active_7d"
    if inactivity <= 14:
        return "at_risk_7d"
    if inactivity <= 30:
        return "at_risk_14d"
    return "inactive_30d_plus"


def paginated_users(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    role: str | None,
    verified: bool | None,
    account_status: str | None,
    signup_start: date | None,
    signup_end: date | None,
    last_active_start: date | None,
    last_active_end: date | None,
    state: str | None,
    sort_by: str,
    sort_order: str,
) -> dict[str, Any]:
    query = db.query(User)
    if search:
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
            )
        )
    if role:
        if role not in {"user", "admin", "superadmin"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role filter")
        query = query.filter(User.role == role)
    if verified is not None:
        query = query.filter(User.is_verified.is_(verified))
    if account_status:
        if account_status not in {"active", "suspended"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid account status")
        query = query.filter(User.account_status == account_status)
    if signup_start:
        query = query.filter(
            User.created_at >= datetime.combine(signup_start, time.min, tzinfo=UTC)
        )
    if signup_end:
        query = query.filter(
            User.created_at
            < datetime.combine(signup_end + timedelta(days=1), time.min, tzinfo=UTC)
        )
    if last_active_start or last_active_end:
        after = (
            datetime.combine(last_active_start, time.min, tzinfo=UTC)
            if last_active_start
            else None
        )
        before = (
            datetime.combine(last_active_end + timedelta(days=1), time.min, tzinfo=UTC)
            if last_active_end
            else None
        )
        query = query.filter(_activity_condition(after=after, before=before))
        if before is not None:
            query = query.filter(not_(_activity_condition(after=before)))
    now = datetime.now(UTC)
    if state:
        if state == "active_7d":
            query = query.filter(_activity_condition(after=now - timedelta(days=7)))
        elif state in {"at_risk_7d", "at_risk_14d", "inactive_30d_plus"}:
            thresholds = {
                "at_risk_7d": (7, 14),
                "at_risk_14d": (14, 30),
                "inactive_30d_plus": (30, None),
            }
            newer, older = thresholds[state]
            query = query.filter(not_(_activity_condition(after=now - timedelta(days=newer))))
            if older is None:
                query = query.filter(_activity_condition(before=now - timedelta(days=newer)))
            else:
                query = query.filter(
                    _activity_condition(after=now - timedelta(days=older), before=now - timedelta(days=newer))
                )
        elif state == "never_active":
            query = query.filter(not_(_activity_condition()))
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid activity state")

    sort_columns = {
        "joined": User.created_at,
        "email": User.email,
        "name": User.full_name,
        "role": User.role,
        "verified": User.is_verified,
        "last_login": User.last_login_at,
    }
    column = sort_columns.get(sort_by)
    if column is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid sort field")
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid sort order")
    total = query.count()
    ordering = asc(column) if sort_order == "asc" else desc(column)
    users = (
        query.order_by(ordering, User.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    last_activity = _last_activity_map(db, [user.id for user in users])
    return {
        "items": [
            {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_verified": user.is_verified,
                "account_status": user.account_status,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "last_meaningful_activity_at": last_activity.get(user.id),
                "activity_state": activity_state(last_activity.get(user.id)),
            }
            for user in users
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }


def require_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def user_overview(db: Session, user: User) -> dict[str, Any]:
    now = datetime.now(UTC)
    workouts = (
        db.query(Workout)
        .filter(
            Workout.user_id == user.id,
            Workout.completed_at.is_not(None),
        )
        .all()
    )
    nutrition = db.query(NutritionLog).filter(NutritionLog.user_id == user.id).all()
    schedules = db.query(ScheduledWorkout).filter(ScheduledWorkout.user_id == user.id).all()
    active_program = (
        db.query(Program)
        .filter(Program.user_id == user.id, Program.is_active.is_(True))
        .order_by(Program.id.desc())
        .first()
    )
    events = (
        db.query(AnalyticsEvent)
        .filter(AnalyticsEvent.user_id == user.id)
        .order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
        .limit(20)
        .all()
    )
    latest_client = next(
        (event for event in events if event.platform or event.app_version), None
    )
    deletion = (
        db.query(AccountDeletionChallenge)
        .filter(AccountDeletionChallenge.user_id == user.id)
        .first()
    )
    last_activity = _last_activity_map(db, [user.id]).get(user.id)
    adopted: set[str] = set()
    if workouts:
        adopted.add("workouts")
    if nutrition:
        adopted.add("nutrition")
    if active_program:
        adopted.add("programs")
    if schedules:
        adopted.add("scheduling")
    event_features = {
        "stats_viewed": "statistics",
        "lab_insights_viewed": "lab_insights",
        "barcode_scan_used": "barcode",
        "micronutrients_viewed": "micronutrients",
        "personal_record_achieved": "personal_records",
    }
    for event in events:
        feature = event_features.get(event.event_name)
        if feature:
            adopted.add(feature)
    meal_count_30 = len(
        {
            (row.date, row.meal_name)
            for row in nutrition
            if row.date >= (now - timedelta(days=29)).date()
        }
    )
    return {
        "account": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "verified": user.is_verified,
            "verified_at": user.verified_at,
            "joined_at": user.created_at,
            "last_login_at": user.last_login_at,
            "last_meaningful_activity_at": last_activity,
            "account_status": user.account_status,
            "latest_platform": latest_client.platform if latest_client else None,
            "latest_app_version": latest_client.app_version if latest_client else None,
        },
        "engagement": {
            "completed_workouts": len(workouts),
            "workouts_last_7_days": sum(1 for row in workouts if row.date >= (now - timedelta(days=6)).date()),
            "workouts_last_30_days": sum(1 for row in workouts if row.date >= (now - timedelta(days=29)).date()),
            "nutrition_entries": len(nutrition),
            "nutrition_logging_days": len({row.date for row in nutrition}),
            "meals_last_30_days": meal_count_30,
            "active_program": (
                {
                    "id": active_program.id,
                    "name": active_program.name,
                    "source_template": active_program.source_template,
                }
                if active_program
                else None
            ),
            "scheduled_workouts": len(schedules),
            "upcoming_scheduled_workouts": sum(1 for row in schedules if row.scheduled_date >= now.date()),
            "last_workout_at": max((row.date for row in workouts), default=None),
            "last_meal_at": max((row.date for row in nutrition), default=None),
            "adopted_features": sorted(adopted),
        },
        "account_state": {
            "deletion_challenge_active": bool(deletion and _utc(deletion.expires_at) > now),
            "deletion_challenge_expires_at": deletion.expires_at if deletion else None,
            "deletion_failed_attempts": deletion.failed_attempts if deletion else None,
            "revoked_token_count": db.query(func.count(RevokedToken.id)).filter(RevokedToken.user_id == user.id).scalar() or 0,
            "token_version": user.token_version,
        },
        "recent_activity": [
            {
                "id": event.id,
                "event_name": event.event_name,
                "occurred_at": event.occurred_at,
                "platform": event.platform,
                "app_version": event.app_version,
                "properties": safe_stored_event_properties(
                    event.event_name, event.properties
                ),
            }
            for event in events
        ],
    }


def page_payload(items: list[Any], *, page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }
