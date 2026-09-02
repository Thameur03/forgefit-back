from sqlalchemy import Boolean, Column, Integer, ForeignKey, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class ScheduledWorkout(Base):
    __tablename__ = "scheduled_workouts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    program_id = Column(Integer, ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    program_day_id = Column(Integer, ForeignKey("program_days.id", ondelete="CASCADE"), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="planned", server_default="planned")
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # Historical schedules were not linked to completed workouts. New rows are
    # trustworthy; migration 010 explicitly marks existing rows otherwise.
    linkage_trustworthy = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    program = relationship("Program")
    program_day = relationship("ProgramDay")

    __table_args__ = (
        UniqueConstraint("user_id", "scheduled_date", name="uq_user_scheduled_date"),
    )
