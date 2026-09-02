from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.sql import func

from database import Base


class LabInsightState(Base):
    """Durable detector identity and lifecycle for one user/subject."""

    __tablename__ = "lab_insight_states"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    detector_id = Column(String(80), nullable=False)
    detector_version = Column(Integer, nullable=False)
    subject_key = Column(String(255), nullable=False)
    first_seen_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_shown_at = Column(DateTime(timezone=True), nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1, server_default="1")
    evidence_fingerprint = Column(String(64), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    last_payload = Column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "detector_id",
            "detector_version",
            "subject_key",
            name="uq_lab_insight_identity",
        ),
    )


class LabAnalysisSnapshot(Base):
    """Versioned deterministic snapshot cached by its source-data watermark."""

    __tablename__ = "lab_analysis_snapshots"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(String(36), nullable=False, unique=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    analytics_version = Column(String(32), nullable=False)
    source_data_watermark = Column(String(64), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    data_through = Column(DateTime(timezone=True), nullable=False)
    stale_after = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "analytics_version",
            "source_data_watermark",
            name="uq_lab_snapshot_source",
        ),
    )
