from sqlalchemy import Column, Integer, ForeignKey, Date, DateTime, UniqueConstraint
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    program = relationship("Program")
    program_day = relationship("ProgramDay")

    __table_args__ = (
        UniqueConstraint("user_id", "scheduled_date", name="uq_user_scheduled_date"),
    )
