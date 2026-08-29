from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class ProgramTemplate(Base):
    __tablename__ = "program_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    weeks = Column(Integer, nullable=True)
    days_per_week = Column(Integer, nullable=True)
    difficulty = Column(String(50), nullable=True)  # beginner, intermediate, advanced
    goal = Column(String(100), nullable=True)       # strength, hypertrophy, endurance
    is_active = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    days = relationship(
        "ProgramTemplateDay",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="ProgramTemplateDay.order_index",
    )


class ProgramTemplateDay(Base):
    __tablename__ = "program_template_days"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("program_templates.id", ondelete="CASCADE"), nullable=False)
    day_number = Column(Integer, nullable=False)
    day_name = Column(String(100), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)

    template = relationship("ProgramTemplate", back_populates="days")
    exercises = relationship(
        "ProgramTemplateExercise",
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="ProgramTemplateExercise.order_index",
    )

    __table_args__ = (
        Index(
            "ux_program_template_days_number",
            "template_id",
            "day_number",
            unique=True,
        ),
        Index(
            "ux_program_template_days_order",
            "template_id",
            "order_index",
            unique=True,
        ),
    )


class ProgramTemplateExercise(Base):
    __tablename__ = "program_template_exercises"

    id = Column(Integer, primary_key=True, index=True)
    day_id = Column(Integer, ForeignKey("program_template_days.id", ondelete="CASCADE"), nullable=False)
    exercise_name = Column(String(255), nullable=False)
    exercise_id = Column(String, nullable=True)
    sets = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    rest_seconds = Column(Integer, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)

    day = relationship("ProgramTemplateDay", back_populates="exercises")

    __table_args__ = (
        Index(
            "ux_program_template_exercises_order",
            "day_id",
            "order_index",
            unique=True,
        ),
    )
