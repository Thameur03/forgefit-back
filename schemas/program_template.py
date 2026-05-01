from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class ProgramTemplateExerciseCreate(BaseModel):
    exercise_name: str
    exercise_id: Optional[str] = None
    sets: int
    reps: int
    rest_seconds: Optional[int] = None
    order_index: int = 0


class ProgramTemplateExerciseUpdate(BaseModel):
    exercise_name: Optional[str] = None
    exercise_id: Optional[str] = None
    sets: Optional[int] = None
    reps: Optional[int] = None
    rest_seconds: Optional[int] = None
    order_index: Optional[int] = None


class ProgramTemplateExerciseSchema(BaseModel):
    id: int
    day_id: int
    exercise_name: str
    exercise_id: Optional[str] = None
    sets: int
    reps: int
    rest_seconds: Optional[int] = None
    order_index: int

    class Config:
        from_attributes = True


class ProgramTemplateDayCreate(BaseModel):
    day_number: int
    day_name: str
    order_index: int = 0


class ProgramTemplateDayUpdate(BaseModel):
    day_number: Optional[int] = None
    day_name: Optional[str] = None
    order_index: Optional[int] = None


class ProgramTemplateDaySchema(BaseModel):
    id: int
    template_id: int
    day_number: int
    day_name: str
    order_index: int
    exercises: List[ProgramTemplateExerciseSchema] = []

    class Config:
        from_attributes = True


class ProgramTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None
    difficulty: Optional[str] = None
    goal: Optional[str] = None
    is_active: bool = True


class ProgramTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None
    difficulty: Optional[str] = None
    goal: Optional[str] = None
    is_active: Optional[bool] = None


class ProgramTemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None
    difficulty: Optional[str] = None
    goal: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    days: List[ProgramTemplateDaySchema] = []

    class Config:
        from_attributes = True


class ProgramTemplateSummary(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None
    difficulty: Optional[str] = None
    goal: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
