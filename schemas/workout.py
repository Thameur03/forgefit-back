from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date, datetime


class WorkoutSetCreate(BaseModel):
    exercise_name: str = Field(..., min_length=1, max_length=200)
    exercise_id: Optional[str] = None
    sets: int
    reps: int
    weight_kg: Optional[float] = Field(default=None, ge=0, le=2000)

    @field_validator("sets")
    @classmethod
    def validate_sets(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("Sets must be between 1 and 20")
        return v

    @field_validator("reps")
    @classmethod
    def validate_reps(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("Reps must be between 1 and 100")
        return v


class LastSessionData(BaseModel):
    date: date
    sets: int
    reps: int
    weight_kg: Optional[float] = None

    class Config:
        from_attributes = True


class WorkoutSetResponse(BaseModel):
    id: int
    exercise_name: str
    exercise_id: Optional[str] = None
    sets: int
    reps: int
    weight_kg: Optional[float] = None
    last_session: Optional[LastSessionData] = None

    class Config:
        from_attributes = True


class WorkoutCreate(BaseModel):
    date: Optional[date] = None  # defaults to today in the endpoint
    notes: Optional[str] = Field(default=None, max_length=500)
    name: Optional[str] = Field(default=None, max_length=255)
    duration_seconds: Optional[int] = Field(default=0, ge=0, le=172800)
    # Stable UUID generated once per logical workout by the client.
    # The same key may be safely retried after a timeout or network failure.
    # Old clients that omit this field create rows with NULL (no deduplication).
    client_request_id: Optional[str] = Field(default=None, max_length=36)
    program_id: Optional[int] = Field(default=None, gt=0)
    program_day_id: Optional[int] = Field(default=None, gt=0)
    scheduled_workout_id: Optional[int] = Field(default=None, gt=0)


class WorkoutUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=500)
    date: Optional[date] = None
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=172800)
    calories_burned: Optional[int] = Field(default=None, ge=0, le=100000)
    completed: Optional[Literal[True]] = None


class WorkoutResponse(BaseModel):
    id: int
    user_id: int
    date: date
    notes: Optional[str] = None
    name: Optional[str] = None
    duration_seconds: Optional[int] = 0
    calories_burned: Optional[int] = 0
    sets: List[WorkoutSetResponse] = Field(default_factory=list)
    total_sets: int = 0
    total_volume_kg: float = 0.0
    client_request_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    program_id: Optional[int] = None
    program_day_id: Optional[int] = None
    scheduled_workout_id: Optional[int] = None

    class Config:
        from_attributes = True


class WorkoutSummary(BaseModel):
    """For list view — no sets detail."""
    id: int
    user_id: int
    date: date
    notes: Optional[str] = None
    name: Optional[str] = None
    duration_seconds: Optional[int] = 0
    calories_burned: Optional[int] = 0
    total_sets: int = 0
    total_volume_kg: float = 0.0
    exercise_count: int = 0
    exercise_names: List[str] = Field(default_factory=list)
    client_request_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    program_id: Optional[int] = None
    program_day_id: Optional[int] = None
    scheduled_workout_id: Optional[int] = None

    class Config:
        from_attributes = True
