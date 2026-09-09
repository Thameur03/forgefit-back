from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AppleTransactionSubmission(BaseModel):
    signed_transaction_info: str = Field(min_length=64, max_length=65536)


class AppleNotificationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signed_payload: str = Field(alias="signedPayload", min_length=64, max_length=65536)


class EntitlementResponse(BaseModel):
    entitlement: Literal["free", "premium"]
    subscription_status: Literal[
        "none", "active", "expired", "revoked", "billing_retry", "grace_period"
    ]
    product_id: str | None = None
    platform: Literal["apple"] | None = None
    environment: str | None = None
    expires_at: datetime | None = None
    grace_expires_at: datetime | None = None
    app_account_token: str


class AppleNotificationResponse(BaseModel):
    accepted: bool = True
    duplicate: bool = False
    outcome: str
