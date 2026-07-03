from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    anonymous_id = Column(String(64), nullable=True)
    session_id = Column(String(64), nullable=True, index=True)
    event_name = Column(String(100), nullable=False, index=True)
    event_category = Column(String(50), nullable=True, index=True)
    screen = Column(String(100), nullable=True)
    properties = Column(JSONB, nullable=True)
    platform = Column(String(20), nullable=True)
    app_version = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        # Composite index for common admin queries
        Index("ix_analytics_events_name_created", "event_name", "created_at"),
        # Composite for user + time queries
        Index("ix_analytics_events_user_created", "user_id", "created_at"),
    )
