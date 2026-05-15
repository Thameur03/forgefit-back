from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.user import User
from schemas.ai_coach import AICoachSummaryResponse
from services.ai_coach import AICoachEngine

router = APIRouter()


@router.get("/coach-summary", response_model=AICoachSummaryResponse)
def get_ai_coach_summary(
    days: int = Query(7, ge=7, le=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if days not in (7, 14, 30):
        raise HTTPException(status_code=422, detail="days must be one of: 7, 14, 30")

    engine = AICoachEngine(db=db, user=current_user, days=days)
    return engine.generate_summary()
