from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel
from schemas.program import ProgramExerciseSchema


class ScheduledWorkoutCreate(BaseModel):
    program_day_id: int
    scheduled_date: date


class ScheduledWorkoutResponse(BaseModel):
    id: int
    user_id: int
    program_id: int
    program_day_id: int
    scheduled_date: date
    day_name: str
    program_name: str
    exercises: list[ProgramExerciseSchema]
    status: str
    completed_at: Optional[datetime] = None
    linkage_trustworthy: bool

    class Config:
        from_attributes = True


class ScheduledDateInfo(BaseModel):
    """Lightweight response for calendar month markers."""
    scheduled_date: date
    day_name: str

    class Config:
        from_attributes = True
