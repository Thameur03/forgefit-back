from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.user import User
from schemas.ai_coach import AICoachSummaryResponse, UnlockStatusResponse
from services.ai_coach import AICoachEngine

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


@router.get("/coach-summary", response_model=AICoachSummaryResponse)
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
