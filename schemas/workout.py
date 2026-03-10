from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date


class WorkoutSetCreate(BaseModel):
    exercise_name: str = Field(..., min_length=1, max_length=200)
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
    sets: int
    reps: int
    weight_kg: Optional[float] = None
    last_session: Optional[LastSessionData] = None

    class Config:
        from_attributes = True


class WorkoutCreate(BaseModel):
    date: Optional[date] = None  # defaults to today in the endpoint
    notes: Optional[str] = Field(default=None, max_length=500)


class WorkoutResponse(BaseModel):
    id: int
    user_id: int
    date: date
    notes: Optional[str] = None
    sets: List[WorkoutSetResponse] = []
    total_sets: int = 0
    total_volume_kg: float = 0.0

    class Config:
        from_attributes = True


class WorkoutSummary(BaseModel):
    """For list view — no sets detail."""
    id: int
    user_id: int
    date: date
    notes: Optional[str] = None
    total_sets: int = 0
    total_volume_kg: float = 0.0

    class Config:
        from_attributes = True
