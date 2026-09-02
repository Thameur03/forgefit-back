from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.user import User
from schemas.ai_coach import AICoachSummaryResponse, UnlockStatusResponse
from schemas.lab_insights_v2 import (
    InsightImpressions,
    LabContextResponse,
    LabContextUpdate,
    LabInsightsV2Response,
)
from services.ai_coach import AICoachEngine
from services.lab_insights_v2 import LabInsightsV2Engine

router = APIRouter()

_UNLOCK_DAYS = 7


def _unlock_status(user: User) -> UnlockStatusResponse:
    """Compute unlock gate based on calendar-date arithmetic."""
    created_date: date = user.created_at.date()
    unlock_date: date = created_date + timedelta(days=_UNLOCK_DAYS)
    today: date = date.today()
    unlocked: bool = today >= unlock_date
    days_remaining: int = max(0, (unlock_date - today).days)
    return UnlockStatusResponse(
        unlocked=unlocked,
        days_remaining=days_remaining,
        unlock_date=unlock_date,
        created_at=created_date,
    )


@router.get("/unlock-status", response_model=UnlockStatusResponse)
def get_unlock_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return whether the user has reached the 7-calendar-day unlock gate."""
    return _unlock_status(current_user)


@router.get(
    "/coach-summary",
    response_model=AICoachSummaryResponse,
    deprecated=True,
)
def get_ai_coach_summary(
    days: int = Query(7, ge=7, le=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if days not in (7, 14, 30):
        raise HTTPException(status_code=422, detail="days must be one of: 7, 14, 30")

    status = _unlock_status(current_user)
    if not status.unlocked:
        raise HTTPException(
            status_code=403,
            detail={
                "locked": True,
                "days_remaining": status.days_remaining,
                "unlock_date": status.unlock_date.isoformat(),
            },
        )

    engine = AICoachEngine(db=db, user=current_user, days=days)
    return engine.generate_summary()


@router.get("/insights/v2", response_model=LabInsightsV2Response)
def get_lab_insights_v2(
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return deterministic evidence-led Lab analysis.

    Unlike the legacy score endpoint, V2 is useful for new users: unavailable
    domains remain explicitly unavailable rather than becoming zero scores.
    """
    engine = LabInsightsV2Engine(db=db, user=current_user)
    try:
        return engine.generate(force_refresh=refresh)
    except Exception:
        db.rollback()
        fallback = engine.stale_fallback()
        if fallback is not None:
            return fallback
        raise


@router.post("/insights/v2/impressions")
def record_lab_insight_impressions(
    data: InsightImpressions,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Only stable structural IDs are accepted; no evidence or health values.
    count = LabInsightsV2Engine(db=db, user=current_user).record_impressions(
        list(dict.fromkeys(data.insight_ids))
    )
    return {"recorded": count}


@router.get("/context", response_model=LabContextResponse)
def get_lab_context(current_user: User = Depends(get_current_user)):
    return {
        "timezone": current_user.timezone,
        "canonical_goal": current_user.canonical_goal,
    }


@router.put("/context", response_model=LabContextResponse)
def update_lab_context(
    data: LabContextUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates = data.model_dump(exclude_unset=True)
    timezone_name = updates.get("timezone")
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=422, detail="Unknown IANA timezone") from exc
    for key, value in updates.items():
        setattr(current_user, key, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(current_user)
    return {
        "timezone": current_user.timezone,
        "canonical_goal": current_user.canonical_goal,
    }
