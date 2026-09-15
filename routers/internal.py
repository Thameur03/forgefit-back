"""Internal server-to-server endpoints — NOT for public use.

All routes in this module are protected by INTERNAL_EMAIL_SECRET and are
hidden from the OpenAPI schema. They must only be called from trusted
server-side infrastructure (e.g. the DAUNTRA website API route).
"""

import logging
import os
import secrets

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from auth.email import send_waitlist_welcome_email

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------

_INTERNAL_SECRET = os.getenv("INTERNAL_EMAIL_SECRET", "").strip()


def require_internal_secret(
    authorization: str = Header(default=""),
) -> None:
    """Validate the shared server-to-server secret.

    Uses secrets.compare_digest to avoid timing-based side-channel attacks.
    Returns 401 for any mismatch — does not reveal whether the secret exists
    or what the expected value is.
    """
    if not _INTERNAL_SECRET:
        # Endpoint is inoperable when unconfigured; fail safe rather than open.
        logger.error("[Internal] INTERNAL_EMAIL_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal email service is not configured",
        )

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    provided = authorization[len(prefix):]
    # constant-time comparison — both sides must be the same type/length class
    if not secrets.compare_digest(
        provided.encode("utf-8"),
        _INTERNAL_SECRET.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class WaitlistWelcomeRequest(BaseModel):
    email: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/email/waitlist-welcome")
def send_waitlist_welcome(
    body: WaitlistWelcomeRequest,
    _: None = Depends(require_internal_secret),
) -> dict:
    """Trigger the DAUNTRA waitlist welcome email for a new signup.

    - Accepts only an email address; all template content is server-controlled.
    - Always returns 200 so the caller's signup flow is never blocked.
    - The `sent` field indicates whether email delivery succeeded.
    """
    # Validate email format before calling the send layer.
    try:
        normalized = validate_email(
            body.email, check_deliverability=False
        ).normalized
    except EmailNotValidError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email address",
        )

    sent = send_waitlist_welcome_email(normalized)
    if not sent:
        logger.warning(
            "[Internal] Waitlist welcome email delivery failed for %s",
            normalized[0] + "***" + normalized[normalized.find("@"):],
        )
    return {"sent": sent}
