"""Admin analytics read endpoints.

All endpoints require admin authentication via get_current_admin.
Normal users receive 403.
No sensitive data (email, passwords, PII) is returned from analytics_events.
Email is joined from the users table only when the admin is authenticated.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from auth.utils import get_current_admin
from database import get_db
from models.analytics_event import AnalyticsEvent
from models.user import User
from schemas.analytics import (
    AnalyticsOverviewResponse, EventCountItem,
    SignupFunnelResponse, FeatureUsageItem, RetentionResponse,
    TopUserItem, RecentEventItem, ErrorEventItem,
    FAILURE_EVENT_NAMES,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)

def _count_event(db: Session, event_name: str, since: datetime) -> int:
    """Count distinct users who triggered a specific event in the time window."""
    return (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.event_name == event_name,
            AnalyticsEvent.created_at >= since,
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )

# ── Overview ──────────────────────────────────────────────────────────────────

@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
def get_overview(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    High-level event counts for the admin overview dashboard.
    Returns total_events, unique_users, average, active_users_24h, top_events.
    """
    since = _cutoff(days)

    total_events: int = (
        db.query(func.count(AnalyticsEvent.id))
        .filter(AnalyticsEvent.created_at >= since)
        .scalar() or 0
    )

    unique_users: int = (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.created_at >= since,
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )

    avg_events = round(total_events / unique_users, 2) if unique_users > 0 else 0.0

    active_users_24h: int = (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.created_at >= _cutoff(1),
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )

    top_raw = (
        db.query(AnalyticsEvent.event_name, func.count(AnalyticsEvent.id).label("cnt"))
        .filter(AnalyticsEvent.created_at >= since)
        .group_by(AnalyticsEvent.event_name)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(10)
        .all()
    )
    top_events = [EventCountItem(event_name=r.event_name, count=r.cnt) for r in top_raw]

    return AnalyticsOverviewResponse(
        total_events=total_events,
        unique_users=unique_users,
        average_events_per_user=avg_events,
        active_users_24h=active_users_24h,
        top_events=top_events,
    )


# ── Signup Funnel ─────────────────────────────────────────────────────────────

@router.get("/analytics/signup-funnel", response_model=SignupFunnelResponse)
def get_signup_funnel(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    Onboarding funnel step counts.
    Steps map to named onboarding events. Anonymous sessions counted via anonymous_id.
    step_1 = signup_started (any session that touched signup)
    step_2 = signup_step_email_completed
    step_3 = signup_step_personal_completed
    step_4 = signup_step_metrics_completed
    step_5 = signup_step_fitness_completed
    completed = sign_up_completed
    total = signup_started (widest funnel top)
    """
    since = _cutoff(days)

    def _count_sessions(event_name: str) -> int:
        """Count unique sessions touching this event (user or anon)."""
        auth_count = (
            db.query(func.count(distinct(AnalyticsEvent.user_id)))
            .filter(
                AnalyticsEvent.event_name == event_name,
                AnalyticsEvent.created_at >= since,
                AnalyticsEvent.user_id.is_not(None),
            )
            .scalar() or 0
        )
        anon_count = (
            db.query(func.count(distinct(AnalyticsEvent.anonymous_id)))
            .filter(
                AnalyticsEvent.event_name == event_name,
                AnalyticsEvent.created_at >= since,
                AnalyticsEvent.user_id.is_(None),
                AnalyticsEvent.anonymous_id.is_not(None),
            )
            .scalar() or 0
        )
        return auth_count + anon_count

    total = _count_sessions("signup_started")
    step_1 = total  # step_1 = signup_started (top of funnel)
    step_2 = _count_sessions("signup_step_email_completed")
    step_3 = _count_sessions("signup_step_personal_completed")
    step_4 = _count_sessions("signup_step_metrics_completed")
    step_5 = _count_sessions("signup_step_fitness_completed")
    completed = _count_sessions("sign_up_completed")

    conversion_rate = round(completed / total, 4) if total > 0 else 0.0

    return SignupFunnelResponse(
        total=total,
        step_1=step_1,
        step_2=step_2,
        step_3=step_3,
        step_4=step_4,
        step_5=step_5,
        completed=completed,
        conversion_rate=conversion_rate,
    )


# ── Feature Usage ─────────────────────────────────────────────────────────────

_FEATURE_EVENTS = [
    "workout_logged",
    "meal_logged",
    "program_activated",
    "lab_insights_viewed",
    "stats_viewed",
    "food_search_used",
    "exercise_search_used",
    "program_created",
]


@router.get("/analytics/feature-usage", response_model=list[FeatureUsageItem])
def get_feature_usage(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Event count + unique users per key feature."""
    since = _cutoff(days)

    rows = (
        db.query(
            AnalyticsEvent.event_name,
            func.count(AnalyticsEvent.id).label("cnt"),
            func.count(distinct(AnalyticsEvent.user_id)).label("uniq"),
        )
        .filter(
            AnalyticsEvent.event_name.in_(_FEATURE_EVENTS),
            AnalyticsEvent.created_at >= since,
        )
        .group_by(AnalyticsEvent.event_name)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .all()
    )

    result = [
        FeatureUsageItem(event_name=r.event_name, count=r.cnt, unique_users=r.uniq)
        for r in rows
    ]
    # Fill zeros for events with no data
    found = {r.event_name for r in rows}
    for ev in _FEATURE_EVENTS:
        if ev not in found:
            result.append(FeatureUsageItem(event_name=ev, count=0, unique_users=0))

    return result


# ── Retention ─────────────────────────────────────────────────────────────────

@router.get("/analytics/retention", response_model=RetentionResponse)
def get_retention(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    DAU / WAU / MAU and stickiness (DAU/MAU).
    Derived server-side from app_opened events (no client tracking needed).
    """
    daily_active = (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.created_at >= _cutoff(1),
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )
    weekly_active = (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.created_at >= _cutoff(7),
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )
    monthly_active = (
        db.query(func.count(distinct(AnalyticsEvent.user_id)))
        .filter(
            AnalyticsEvent.created_at >= _cutoff(30),
            AnalyticsEvent.user_id.is_not(None),
        )
        .scalar() or 0
    )
    stickiness = round(daily_active / monthly_active, 4) if monthly_active > 0 else 0.0

    return RetentionResponse(
        daily_active_users=daily_active,
        weekly_active_users=weekly_active,
        monthly_active_users=monthly_active,
        stickiness=stickiness,
    )


# ── Top Users ─────────────────────────────────────────────────────────────────

@router.get("/analytics/top-users", response_model=list[TopUserItem])
def get_top_users(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    Most active users by event count.
    Email is joined from users table — not stored in analytics_events.
    """
    since = _cutoff(days)

    rows = (
        db.query(
            AnalyticsEvent.user_id,
            func.count(AnalyticsEvent.id).label("event_count"),
        )
        .filter(
            AnalyticsEvent.created_at >= since,
            AnalyticsEvent.user_id.is_not(None),
        )
        .group_by(AnalyticsEvent.user_id)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(limit)
        .all()
    )

    if not rows:
        return []

    user_ids = [r.user_id for r in rows]
    users = {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_(user_ids)).all()
    }

    return [
        TopUserItem(
            user_id=r.user_id,
            email=users.get(r.user_id, "unknown"),
            event_count=r.event_count,
        )
        for r in rows
    ]


# ── Recent Events ─────────────────────────────────────────────────────────────

@router.get("/analytics/recent-events", response_model=list[RecentEventItem])
def get_recent_events(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    Most recent analytics events (newest first).
    Email joined from users table for authenticated events;
    shows 'anonymous' for pre-signup events.
    Properties shown as stored — already sanitized at ingestion.
    """
    events = (
        db.query(AnalyticsEvent)
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(limit)
        .all()
    )

    # Batch-load user emails to avoid N+1
    user_ids = {e.user_id for e in events if e.user_id is not None}
    users = {}
    if user_ids:
        users = {
            u.id: u.email
            for u in db.query(User).filter(User.id.in_(user_ids)).all()
        }

    return [
        RecentEventItem(
            id=e.id,
            event_name=e.event_name,
            user_id=e.user_id,
            email=users.get(e.user_id, "anonymous") if e.user_id else "anonymous",
            timestamp=e.created_at.isoformat(),
            metadata=e.properties or {},
        )
        for e in events
    ]


# ── Errors (safe failure events) ──────────────────────────────────────────────

@router.get("/analytics/errors", response_model=list[ErrorEventItem])
def get_errors(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    Aggregate safe failure event counts grouped by event_name and error_code.
    Only includes known failure event names.
    Never returns raw exception text — only safe error_code values stored at ingestion.
    """
    since = _cutoff(days)

    # Extract error_code from JSONB properties safely
    # SQLAlchemy 2 / PostgreSQL JSONB: access via subscript
    error_code_expr = AnalyticsEvent.properties["error_code"].as_string()

    rows = (
        db.query(
            AnalyticsEvent.event_name,
            error_code_expr.label("error_code"),
            func.count(AnalyticsEvent.id).label("cnt"),
            func.count(distinct(AnalyticsEvent.user_id)).label("uniq"),
        )
        .filter(
            AnalyticsEvent.event_name.in_(FAILURE_EVENT_NAMES),
            AnalyticsEvent.created_at >= since,
        )
        .group_by(AnalyticsEvent.event_name, error_code_expr)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .all()
    )

    return [
        ErrorEventItem(
            event_name=r.event_name,
            error_code=r.error_code,
            count=r.cnt,
            unique_users=r.uniq,
        )
        for r in rows
    ]
