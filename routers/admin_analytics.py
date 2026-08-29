"""Admin-protected, privacy-safe product analytics endpoints."""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.utils import get_current_admin
from database import get_db
from models.analytics_event import AnalyticsEvent
from models.user import User
from schemas.admin_analytics import (
    AnalyticsEventPage,
    ErrorAnalyticsResponse,
    ExecutiveOverviewResponse,
    FeatureAdoptionResponse,
    GrowthResponse,
    InsightsAnalyticsResponse,
    NutritionAnalyticsResponse,
    ProgramAnalyticsResponse,
    RetentionV2Response,
    SchedulingAnalyticsResponse,
    SignupFunnelV2Response,
    WorkoutAnalyticsResponse,
)
from schemas.analytics import TopUserItem
from services.admin_metrics import (
    DateRange,
    error_analytics,
    event_page,
    executive_overview,
    feature_adoption,
    growth,
    insights_analytics,
    nutrition_analytics,
    program_analytics,
    resolve_date_range,
    retention,
    scheduling_analytics,
    signup_funnel,
    workout_analytics,
)
from services.analytics_events import MEANINGFUL_ACTIVITY_EVENTS


router = APIRouter()


def _range(
    db: Session,
    *,
    start_date: date | None,
    end_date: date | None,
    period: str,
    days: int | None,
) -> DateRange:
    # ``days`` preserves compatibility with the first admin client while all
    # new UI uses the explicit date range/preset contract.
    if days is not None and start_date is None and end_date is None:
        today = datetime.now(timezone.utc).date()
        return DateRange(today - timedelta(days=days - 1), today)
    return resolve_date_range(
        db,
        start_date=start_date,
        end_date=end_date,
        period=period,
    )


def _params(
    db: Session,
    start_date: date | None,
    end_date: date | None,
    period: str,
    days: int | None,
) -> DateRange:
    return _range(
        db,
        start_date=start_date,
        end_date=end_date,
        period=period,
        days=days,
    )


@router.get("/analytics/overview", response_model=ExecutiveOverviewResponse)
def get_overview(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return executive_overview(db, _params(db, start_date, end_date, period, days))


@router.get("/analytics/growth", response_model=GrowthResponse)
def get_growth(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return growth(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/signup-funnel", response_model=SignupFunnelV2Response)
@router.get("/analytics/funnel", response_model=SignupFunnelV2Response)
def get_signup_funnel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return signup_funnel(db, _params(db, start_date, end_date, period, days))


@router.get("/analytics/retention", response_model=RetentionV2Response)
def get_retention(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("90d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return retention(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/feature-usage", response_model=FeatureAdoptionResponse)
@router.get("/analytics/features", response_model=FeatureAdoptionResponse)
def get_features(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return feature_adoption(db, _params(db, start_date, end_date, period, days))


@router.get("/analytics/workouts", response_model=WorkoutAnalyticsResponse)
def get_workouts(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return workout_analytics(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/nutrition", response_model=NutritionAnalyticsResponse)
def get_nutrition(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return nutrition_analytics(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/programs", response_model=ProgramAnalyticsResponse)
def get_programs(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return program_analytics(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/scheduling", response_model=SchedulingAnalyticsResponse)
def get_scheduling(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return scheduling_analytics(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/insights", response_model=InsightsAnalyticsResponse)
def get_insights(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return insights_analytics(db, _params(db, start_date, end_date, period, None))


@router.get("/analytics/events", response_model=AnalyticsEventPage)
@router.get("/analytics/recent-events", response_model=AnalyticsEventPage)
def get_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    event_name: Optional[str] = Query(None, max_length=100),
    user_id: Optional[int] = Query(None, ge=1),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    value = _params(db, start_date, end_date, period, None)
    return event_page(
        db,
        value,
        page=page,
        page_size=page_size,
        event_name=event_name,
        user_id=user_id,
    )


@router.get("/analytics/errors", response_model=ErrorAnalyticsResponse)
def get_errors(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: str = Query("30d"),
    days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return error_analytics(db, _params(db, start_date, end_date, period, days))


@router.get("/analytics/top-users", response_model=list[TopUserItem])
def get_top_users(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            AnalyticsEvent.user_id,
            func.count(AnalyticsEvent.id).label("event_count"),
        )
        .join(User, User.id == AnalyticsEvent.user_id)
        .filter(
            User.role == "user",
            AnalyticsEvent.user_id.is_not(None),
            AnalyticsEvent.event_name.in_(MEANINGFUL_ACTIVITY_EVENTS),
            AnalyticsEvent.occurred_at >= since,
        )
        .group_by(AnalyticsEvent.user_id)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(limit)
        .all()
    )
    emails = (
        {
            user.id: user.email
            for user in db.query(User)
            .filter(User.id.in_([row.user_id for row in rows]))
            .all()
        }
        if rows
        else {}
    )
    return [
        TopUserItem(
            user_id=row.user_id,
            email=emails.get(row.user_id, "unknown"),
            event_count=row.event_count,
        )
        for row in rows
    ]
