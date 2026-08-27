import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.analytics_event import AnalyticsEvent
from models.user import User
from schemas.analytics import AnalyticsEventCreate, PublicAnalyticsEventCreate

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Authenticated event ingestion ─────────────────────────────────────────────

@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def ingest_event(
    payload: AnalyticsEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Store an analytics event for an authenticated user.
    Returns 202 immediately — caller should fire-and-forget.
    Never raises 500 to the caller.
    """
    try:
        event = AnalyticsEvent(
            user_id=current_user.id,
            session_id=payload.session_id,
            event_name=payload.event_name,
            event_category=payload.event_category,
            screen=payload.screen,
            properties=payload.properties,
            platform=payload.platform,
            app_version=payload.app_version,
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        # Log internally but never surface to caller
        logger.error("[Analytics] Authenticated event storage failed (%s)", type(exc).__name__)
        db.rollback()

    return {"status": "accepted"}


# ── Public (anonymous / pre-auth) event ingestion ────────────────────────────

@router.post("/events/public", status_code=status.HTTP_202_ACCEPTED)
def ingest_public_event(
    payload: PublicAnalyticsEventCreate,
    db: Session = Depends(get_db),
):
    """
    Store a pre-authentication analytics event.
    Only accepts the restricted public event allowlist.
    Requires anonymous_id and session_id — no JWT needed.
    Returns 202 immediately — caller should fire-and-forget.
    """
    try:
        event = AnalyticsEvent(
            anonymous_id=payload.anonymous_id,
            session_id=payload.session_id,
            event_name=payload.event_name,
            event_category=payload.event_category,
            screen=payload.screen,
            properties=payload.properties,
            platform=payload.platform,
            app_version=payload.app_version,
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        logger.error("[Analytics] Public event storage failed (%s)", type(exc).__name__)
        db.rollback()

    return {"status": "accepted"}
