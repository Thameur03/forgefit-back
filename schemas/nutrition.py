from enum import Enum
from datetime import date as _Date
from typing import Optional, List, Dict

from pydantic import BaseModel, Field


class MealType(str, Enum):
    """Kept for backwards compatibility with existing data."""
    breakfast = "Breakfast"
    lunch = "Lunch"
    dinner = "Dinner"
    snack = "Snack"


class NutritionLogCreate(BaseModel):
    # IMPORTANT: field named 'date' must NOT shadow the type annotation.
    # Use the _Date alias imported above so Pydantic v2 resolves the type
    # correctly instead of collapsing it to NoneType (which would cause the
    # "Input should be None" validation error when a real date string is sent).
    date: Optional[_Date] = None   # defaults to today in the endpoint
    meal_name: str = Field(..., min_length=1, max_length=100)
    food_name: str = Field(..., min_length=1, max_length=200)
    calories: float = Field(..., gt=0, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=2000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=2000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=2000)
    fdc_id: Optional[int] = None
    # Idempotency key from the Flutter client. When non-null the backend uses
    # a partial unique index (user_id, client_request_id WHERE NOT NULL) to
    # return the existing row instead of inserting a duplicate.
    # Null = legacy / bulk behaviour: always insert.
    client_request_id: Optional[str] = Field(default=None, max_length=36)


class NutritionLogResponse(BaseModel):
    id: int
    user_id: int
    date: _Date
    meal_name: str
    food_name: str
    calories: float
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fdc_id: Optional[int] = None

    class Config:
        from_attributes = True


class DailySummary(BaseModel):
    date: _Date
    total_calories: float = 0.0
    total_protein_g: float = 0.0
    total_carbs_g: float = 0.0
    total_fat_g: float = 0.0
    logs: List[NutritionLogResponse] = []
    meals: Dict[str, List[NutritionLogResponse]] = {}
