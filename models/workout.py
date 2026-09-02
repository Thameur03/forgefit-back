from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from database import Base


class Workout(Base):
    __tablename__ = "workouts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    notes = Column(String, nullable=True)
    name = Column(String(255), nullable=True)
    duration_seconds = Column(Integer, default=0)
    # Null while the start-created workout shell is still a draft. Set exactly
    # once by the explicit finalization request.
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completion_inferred = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Idempotency key sent by the Flutter client.
    # Nullable so legacy rows (client_request_id IS NULL) are unaffected.
    # Uniqueness enforced by a partial PostgreSQL index (user_id, client_request_id)
    # WHERE client_request_id IS NOT NULL — see Alembic migration 003.
    client_request_id = Column(String(36), nullable=True, index=False)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=True)
    program_day_id = Column(Integer, ForeignKey("program_days.id"), nullable=True)
    scheduled_workout_id = Column(
        Integer, ForeignKey("scheduled_workouts.id"), nullable=True
    )

    sets = relationship(
        "WorkoutSet", back_populates="workout", cascade="all, delete-orphan"
    )


class WorkoutSet(Base):
    __tablename__ = "workout_sets"

    id = Column(Integer, primary_key=True, index=True)
    workout_id = Column(
        Integer, ForeignKey("workouts.id", ondelete="CASCADE"), nullable=False
    )
    exercise_name = Column(String, index=True, nullable=False)
    exercise_id = Column(String, index=True, nullable=True)
    sets = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    weight_kg = Column(Float, nullable=True)

    workout = relationship("Workout", back_populates="sets")
