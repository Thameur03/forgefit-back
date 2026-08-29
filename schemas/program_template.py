from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class ProgramTemplateExerciseCreate(BaseModel):
    exercise_name: str = Field(..., min_length=1, max_length=255)
    exercise_id: Optional[str] = Field(None, max_length=100)
    sets: int = Field(..., ge=1, le=100)
    reps: int = Field(..., ge=1, le=1000)
    rest_seconds: Optional[int] = Field(None, ge=0, le=3600)
    order_index: int = Field(0, ge=0, le=1000)


class ProgramTemplateExerciseUpdate(BaseModel):
    exercise_name: Optional[str] = Field(None, min_length=1, max_length=255)
    exercise_id: Optional[str] = Field(None, max_length=100)
    sets: Optional[int] = Field(None, ge=1, le=100)
    reps: Optional[int] = Field(None, ge=1, le=1000)
    rest_seconds: Optional[int] = Field(None, ge=0, le=3600)
    order_index: Optional[int] = Field(None, ge=0, le=1000)


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
    day_number: int = Field(..., ge=1, le=31)
    day_name: str = Field(..., min_length=1, max_length=100)
    order_index: int = Field(0, ge=0, le=31)


class ProgramTemplateDayUpdate(BaseModel):
    day_number: Optional[int] = Field(None, ge=1, le=31)
    day_name: Optional[str] = Field(None, min_length=1, max_length=100)
    order_index: Optional[int] = Field(None, ge=0, le=31)


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
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    weeks: Optional[int] = Field(None, ge=1, le=104)
    days_per_week: Optional[int] = Field(None, ge=1, le=7)
    difficulty: Optional[str] = Field(None, pattern=r"^(beginner|intermediate|advanced)$")
    goal: Optional[str] = Field(None, min_length=1, max_length=100)
    is_active: bool = False


class ProgramTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    weeks: Optional[int] = Field(None, ge=1, le=104)
    days_per_week: Optional[int] = Field(None, ge=1, le=7)
    difficulty: Optional[str] = Field(None, pattern=r"^(beginner|intermediate|advanced)$")
    goal: Optional[str] = Field(None, min_length=1, max_length=100)
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
