"""Authenticated and verified-email customer account deletion."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth.email import (
    email_configuration_issue,
    email_delivery_configured,
    send_account_deletion_email,
)
from auth.otp import generate_numeric_otp, otp_has_expired
from auth.utils import get_current_user, hash_password, verify_password
from config import normalize_origin, parse_cors_origins
from database import get_db
from limiter import limiter
from models.account_deletion import AccountDeletionChallenge
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.program import Program
from models.schedule import ScheduledWorkout
from models.token import RevokedToken
from models.user import User
from models.workout import Workout
from schemas.user import AccountDeletionConfirm, AccountDeletionRequest, MessageResponse

router = APIRouter()
logger = logging.getLogger(__name__)
_CHALLENGE_EXPIRY_MINUTES = 15
_MAX_FAILED_ATTEMPTS = 5
_REQUEST_MESSAGE = (
    "If that email belongs to a Jugurtha Fit account, a confirmation code has been sent."
)
_INVALID_MESSAGE = "Invalid or expired deletion code"


def _delete_user_owned_records(db: Session, user: User) -> None:
    """Stage complete current-schema deletion; the caller owns the transaction."""
    user_id = user.id
    db.query(ScheduledWorkout).filter(ScheduledWorkout.user_id == user_id).delete(
        synchronize_session=False
    )
    for program in db.query(Program).filter(Program.user_id == user_id).all():
        db.delete(program)
    for workout in db.query(Workout).filter(Workout.user_id == user_id).all():
        db.delete(workout)
    db.query(NutritionLog).filter(NutritionLog.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(RevokedToken).filter(RevokedToken.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(AnalyticsEvent).filter(AnalyticsEvent.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(AccountDeletionChallenge).filter(
        AccountDeletionChallenge.user_id == user_id
    ).delete(synchronize_session=False)
    db.flush()
    db.delete(user)
    db.flush()


def _browser_origin_allowed(request: Request) -> bool:
    raw = request.headers.get("origin")
    if not raw:
        return True
    try:
        origin = normalize_origin(raw)
        api_origin = normalize_origin(str(request.base_url).rstrip("/"))
    except ValueError:
        return False
    return origin == api_origin or origin in set(parse_cors_origins())


def _require_public_json(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0]
    if media_type.strip().lower() != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")
    if not _browser_origin_allowed(request):
        raise HTTPException(403, "Request origin is not allowed")


@router.post(
    "/deletion/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("3/hour")
def request_account_deletion(
    request: Request,
    data: AccountDeletionRequest,
    db: Session = Depends(get_db),
):
    """Send a short-lived code without disclosing account existence."""
    _require_public_json(request)
    if not email_delivery_configured():
        logger.error(
            "[Account deletion] Service unavailable: %s",
            email_configuration_issue(),
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Account deletion requests are temporarily unavailable. Please use the support contact shown on the deletion page.",
        )
    user = db.query(User).filter(User.email == data.email).first()
    if user is None:
        return {"message": _REQUEST_MESSAGE}
    code = generate_numeric_otp()
    db.query(AccountDeletionChallenge).filter(
        AccountDeletionChallenge.user_id == user.id
    ).delete(synchronize_session=False)
    db.add(
        AccountDeletionChallenge(
            user_id=user.id,
            code_hash=hash_password(code),
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=_CHALLENGE_EXPIRY_MINUTES),
        )
    )
    db.flush()
    if send_account_deletion_email(user.email, code):
        db.commit()
    else:
        db.rollback()
        logger.warning("[Account deletion] Confirmation email delivery failed")
    return {"message": _REQUEST_MESSAGE}


@router.post(
    "/deletion/confirm",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("5/minute")
def confirm_account_deletion(
    request: Request,
    data: AccountDeletionConfirm,
    db: Session = Depends(get_db),
):
    """Hard-delete only the account bound to a valid one-time email code."""
    _require_public_json(request)
    user = db.query(User).filter(User.email == data.email).first()
    challenge = None
    if user is not None:
        challenge = db.query(AccountDeletionChallenge).filter(
            AccountDeletionChallenge.user_id == user.id
        ).first()
    if user is None or challenge is None:
        raise HTTPException(400, _INVALID_MESSAGE)
    if challenge.failed_attempts >= _MAX_FAILED_ATTEMPTS or otp_has_expired(
        challenge.expires_at
    ):
        db.delete(challenge)
        db.commit()
        raise HTTPException(400, _INVALID_MESSAGE)
    if not verify_password(data.code, challenge.code_hash):
        challenge.failed_attempts += 1
        if challenge.failed_attempts >= _MAX_FAILED_ATTEMPTS:
            db.delete(challenge)
        db.commit()
        raise HTTPException(400, _INVALID_MESSAGE)
    try:
        _delete_user_owned_records(db, user)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Public account deletion transaction rolled back (%s)",
            type(exc).__name__,
        )
        raise HTTPException(500, "Account deletion failed; no data was deleted") from exc
    return {"message": "Account and associated data were permanently deleted."}


@router.delete("/me", response_model=MessageResponse, status_code=200)
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        _delete_user_owned_records(db, current_user)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Account deletion transaction rolled back (%s)",
            type(exc).__name__,
        )
        raise HTTPException(500, "Account deletion failed; no data was deleted") from exc
    return {"message": "Account and associated data were permanently deleted."}
