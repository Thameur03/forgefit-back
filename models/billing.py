from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from database import Base


class BillingIdentity(Base):
    """Stable, opaque DAUNTRA-user binding used as Apple's appAccountToken."""

    __tablename__ = "billing_identities"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    app_account_token = Column(String(36), nullable=False, unique=True, index=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppleSubscription(Base):
    """Latest verified state for one Apple original transaction lineage."""

    __tablename__ = "apple_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(
        String(16), nullable=False, default="apple", server_default="apple"
    )
    app_account_token = Column(String(36), nullable=False, index=True)
    original_transaction_id = Column(
        String(128), nullable=False, unique=True, index=True
    )
    latest_transaction_id = Column(
        String(128), nullable=False, unique=True, index=True
    )
    product_id = Column(String(64), nullable=False)
    environment = Column(String(24), nullable=False)
    storefront = Column(String(16), nullable=True)
    status = Column(String(24), nullable=False)
    purchased_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    grace_expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_signed_at = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AppleNotificationEvent(Base):
    """Minimal replay ledger for cryptographically verified Apple callbacks."""

    __tablename__ = "apple_notification_events"

    id = Column(Integer, primary_key=True)
    notification_uuid = Column(String(64), nullable=False, unique=True, index=True)
    notification_type = Column(String(64), nullable=False)
    subtype = Column(String(64), nullable=True)
    signed_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(32), nullable=False)
    received_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
