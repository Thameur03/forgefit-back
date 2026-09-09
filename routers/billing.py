import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from limiter import limiter
from models.user import User
from schemas.billing import (
    AppleNotificationRequest,
    AppleNotificationResponse,
    AppleTransactionSubmission,
    EntitlementResponse,
)
from services.apple_billing import (
    AppleAccountBindingError,
    AppleBillingConfigurationError,
    AppleBillingService,
    AppleTransactionRejected,
    AppleVerificationError,
    get_apple_billing_service,
)


router = APIRouter()
logger = logging.getLogger(__name__)


def billing_service_dependency() -> AppleBillingService:
    try:
        return get_apple_billing_service()
    except AppleBillingConfigurationError as exc:
        logger.error("[Apple billing] Configuration unavailable: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Apple subscription verification is temporarily unavailable",
        ) from exc


def _map_billing_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AppleVerificationError):
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_400_BAD_REQUEST
        )
        detail = (
            "Apple subscription verification is temporarily unavailable"
            if exc.retryable
            else "Apple signed data could not be verified"
        )
        return HTTPException(code, detail)
    if isinstance(exc, AppleAccountBindingError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            "This Apple purchase is bound to a different DAUNTRA account",
        )
    if isinstance(exc, AppleTransactionRejected):
        return HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Apple transaction is not valid for DAUNTRA Premium",
        )
    logger.error("[Apple billing] Unexpected failure (%s)", type(exc).__name__)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Apple subscription verification is temporarily unavailable",
    )


@router.get("/entitlement", response_model=EntitlementResponse)
@limiter.limit("60/minute")
def get_entitlement(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    service: AppleBillingService = Depends(billing_service_dependency),
):
    try:
        return service.entitlement_for_user(db, current_user.id).to_dict()
    except Exception as exc:
        db.rollback()
        raise _map_billing_error(exc) from exc


@router.post("/apple/transaction", response_model=EntitlementResponse)
@limiter.limit("30/minute")
def submit_apple_transaction(
    request: Request,
    payload: AppleTransactionSubmission,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    service: AppleBillingService = Depends(billing_service_dependency),
):
    try:
        return service.submit_transaction(
            db,
            user_id=current_user.id,
            signed_transaction=payload.signed_transaction_info,
        ).to_dict()
    except Exception as exc:
        db.rollback()
        raise _map_billing_error(exc) from exc


@router.post(
    "/apple/notifications",
    response_model=AppleNotificationResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
def receive_apple_notification(
    request: Request,
    payload: AppleNotificationRequest,
    db: Session = Depends(get_db),
    service: AppleBillingService = Depends(billing_service_dependency),
):
    """Authenticate exclusively with Apple's signed payload, never user JWT."""
    try:
        duplicate, outcome = service.process_notification(
            db, signed_payload=payload.signed_payload
        )
        return {
            "accepted": True,
            "duplicate": duplicate,
            "outcome": outcome,
        }
    except Exception as exc:
        db.rollback()
        raise _map_billing_error(exc) from exc
