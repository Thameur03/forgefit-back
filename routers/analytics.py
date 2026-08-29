import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from limiter import limiter
from models.analytics_event import AnalyticsEvent
from models.user import User
from schemas.analytics import (
    AnalyticsEventCreate,
    AnalyticsIdentityLink,
    PublicAnalyticsEventCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter()
_MAX_ANALYTICS_PAYLOAD_BYTES = 16 * 1024
_IDENTITY_LINK_WINDOW = timedelta(days=7)


def _require_bounded_payload(request: Request) -> None:
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        return
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Content-Length") from exc
    if length > _MAX_ANALYTICS_PAYLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Analytics payload too large")


def _link_session_events(
    db: Session,
    *,
    current_user: User,
    anonymous_id: str,
    session_id: str,
) -> int:
    """Claim only recent, unclaimed rows from one exact high-entropy session."""
    now = datetime.now(timezone.utc)
    return (
        db.query(AnalyticsEvent)
        .filter(
            AnalyticsEvent.user_id.is_(None),
            AnalyticsEvent.anonymous_id == anonymous_id,
            AnalyticsEvent.session_id == session_id,
            AnalyticsEvent.occurred_at >= now - _IDENTITY_LINK_WINDOW,
        )
        .update(
            {
                AnalyticsEvent.user_id: current_user.id,
                AnalyticsEvent.identity_linked_at: now,
            },
            synchronize_session=False,
        )
    )


def _store_event(
    db: Session,
    payload: AnalyticsEventCreate | PublicAnalyticsEventCreate,
    *,
    user_id: int | None,
) -> bool:
    """Persist once. Return False for an idempotent duplicate retry."""
    client_event_id = str(payload.client_event_id) if payload.client_event_id else None
    if client_event_id is not None:
        duplicate = db.query(AnalyticsEvent.id).filter(
            AnalyticsEvent.client_event_id == client_event_id
        ).first()
        if duplicate is not None:
            db.commit()
            return False
    event = AnalyticsEvent(
        client_event_id=client_event_id,
        user_id=user_id,
        anonymous_id=getattr(payload, "anonymous_id", None),
        session_id=payload.session_id,
        event_name=payload.event_name,
        event_category=payload.event_category,
        screen=payload.screen,
        properties=payload.properties,
        platform=payload.platform,
        app_version=payload.app_version,
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
    )
    db.add(event)
    db.commit()
    return True


# ── Authenticated event ingestion ─────────────────────────────────────────────

@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("120/minute")
def ingest_event(
    request: Request,
    payload: AnalyticsEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Store an analytics event for an authenticated user.
    Returns 202 immediately — caller should fire-and-forget.
    Never raises 500 to the caller.
    """
    _require_bounded_payload(request)
    try:
        linked = 0
        if payload.anonymous_id and payload.session_id:
            linked = _link_session_events(
                db,
                current_user=current_user,
                anonymous_id=payload.anonymous_id,
                session_id=payload.session_id,
            )
        stored = _store_event(db, payload, user_id=current_user.id)
    except IntegrityError:
        # A concurrent retry can race the pre-insert duplicate check.
        db.rollback()
        stored = False
        linked = 0
    except Exception as exc:
        # Log internally but never surface to caller
        logger.error("[Analytics] Authenticated event storage failed (%s)", type(exc).__name__)
        db.rollback()

        stored = False
        linked = 0

    return {"status": "accepted", "duplicate": not stored, "linked_events": linked}


# ── Public (anonymous / pre-auth) event ingestion ────────────────────────────

@router.post("/events/public", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("60/minute")
def ingest_public_event(
    request: Request,
    payload: PublicAnalyticsEventCreate,
    db: Session = Depends(get_db),
):
    """
    Store a pre-authentication analytics event.
    Only accepts the restricted public event allowlist.
    Requires anonymous_id and session_id — no JWT needed.
    Returns 202 immediately — caller should fire-and-forget.
    """
    _require_bounded_payload(request)
    try:
        stored = _store_event(db, payload, user_id=None)
    except IntegrityError:
        db.rollback()
        stored = False
    except Exception as exc:
        logger.error("[Analytics] Public event storage failed (%s)", type(exc).__name__)
        db.rollback()

        stored = False

    return {"status": "accepted", "duplicate": not stored}


@router.post("/identity/link", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
def link_analytics_identity(
    request: Request,
    payload: AnalyticsIdentityLink,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Link one recent anonymous session to the authenticated account.

    The persistent anonymous device id is never used by itself. Requiring the
    exact session id prevents two accounts on one device from being merged.
    Already-linked rows cannot be reassigned.
    """
    _require_bounded_payload(request)
    linked = _link_session_events(
        db,
        current_user=current_user,
        anonymous_id=payload.anonymous_id,
        session_id=payload.session_id,
    )
    db.commit()
    return {"linked_events": linked}
