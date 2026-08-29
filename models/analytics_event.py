from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    # Client-generated idempotency key. Nullable for legacy clients/rows.
    client_event_id = Column(String(64), nullable=True, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    anonymous_id = Column(String(64), nullable=True, index=True)
    session_id = Column(String(64), nullable=True, index=True)
    event_name = Column(String(100), nullable=False, index=True)
    event_category = Column(String(50), nullable=True, index=True)
    screen = Column(String(100), nullable=True)
    # PostgreSQL keeps native JSONB; SQLite-based integration tests use the
    # portable JSON type without changing production storage semantics.
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    platform = Column(String(20), nullable=True)
    app_version = Column(String(20), nullable=True)
    # occurred_at is the bounded client event time used in product metrics.
    # created_at remains the immutable server receipt time.
    occurred_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    identity_linked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        # Composite index for common admin queries
        Index("ix_analytics_events_name_occurred", "event_name", "occurred_at"),
        # Composite for user + time queries
        Index("ix_analytics_events_user_occurred", "user_id", "occurred_at"),
        # Exact session-scoped identity stitching.
        Index(
            "ix_analytics_events_anon_session",
            "anonymous_id",
            "session_id",
        ),
    )
