from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from database import Base


class OperationalEvent(Base):
    """Privacy-safe operational failures and delivery outcomes.

    No user identifiers, addresses, secrets, message bodies, or raw exception
    text belong in this table.
    """

    __tablename__ = "operational_events"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False, index=True)
    event_name = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_operational_event_created", "event_name", "created_at"),
    )
