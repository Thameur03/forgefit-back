from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    weeks = Column(Integer, nullable=True)
    days_per_week = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=False, nullable=False, server_default="false")
    source_template = Column(String(100), nullable=True)  # e.g. "push_pull_legs"
    days = relationship(
        "ProgramDay",
        back_populates="program",
        cascade="all, delete-orphan",
        order_by="ProgramDay.day_number",
    )


class ProgramDay(Base):
    __tablename__ = "program_days"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    day_number = Column(Integer, nullable=False)  # 1, 2, 3 ...
    day_name = Column(String(100), nullable=False)  # "Push Day", "Day A"
    program = relationship("Program", back_populates="days")
    exercises = relationship(
        "ProgramExercise",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="ProgramExercise.order_index",
    )


class ProgramExercise(Base):
    __tablename__ = "program_exercises"
    id = Column(Integer, primary_key=True, index=True)
    program_day_id = Column(Integer, ForeignKey("program_days.id"), nullable=False)
    exercise_name = Column(String(255), nullable=False)
    sets = Column(Integer, nullable=False, default=3)
    reps = Column(Integer, nullable=False, default=8)
    weight_kg = Column(Float, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
    day = relationship("ProgramDay", back_populates="exercises")
