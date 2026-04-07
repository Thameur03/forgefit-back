from typing import Optional
from pydantic import BaseModel

class ProgramExerciseSchema(BaseModel):
    id: int
    exercise_name: str
    exercise_id: Optional[str] = None
    sets: int
    reps: int
    weight_kg: Optional[float]
    order_index: int

    class Config:
        from_attributes = True

class ProgramDaySchema(BaseModel):
    id: int
    day_number: int
    day_name: str
    exercises: list[ProgramExerciseSchema]

    class Config:
        from_attributes = True

class ProgramResponse(BaseModel):
    id: int
    name: str
    weeks: Optional[int]
    days_per_week: Optional[int]
    is_active: bool
    source_template: Optional[str]
    days: list[ProgramDaySchema]

    class Config:
        from_attributes = True

class ProgramSummary(BaseModel):
    id: int
    name: str
    weeks: Optional[int]
    days_per_week: Optional[int]
    is_active: bool
    source_template: Optional[str]

    class Config:
        from_attributes = True

class CreateProgramBody(BaseModel):
    name: str
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None

class AddExerciseBody(BaseModel):
    exercise_name: str
    exercise_id: Optional[str] = None
    sets: int = 3
    reps: int = 8
    weight_kg: Optional[float] = None
    order_index: int = 0

class UpdateExerciseBody(BaseModel):
    exercise_name: Optional[str] = None
    exercise_id: Optional[str] = None
    sets: Optional[int] = None
    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    order_index: Optional[int] = None

class UpdateProgramBody(BaseModel):
    name: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None

class AddDayBody(BaseModel):
    day_number: int
    day_name: str
