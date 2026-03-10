from enum import Enum
from datetime import date
from typing import Optional, List, Dict

from pydantic import BaseModel, Field, field_validator


class MealType(str, Enum):
    breakfast = "Breakfast"
    lunch = "Lunch"
    dinner = "Dinner"
    snack = "Snack"


class NutritionLogCreate(BaseModel):
    date: Optional[date] = None  # defaults to today in the endpoint
    meal_name: MealType
    food_name: str = Field(..., min_length=1, max_length=200)
    calories: float = Field(..., gt=0, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=2000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=2000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=2000)


class NutritionLogResponse(BaseModel):
    id: int
    user_id: int
    date: date
    meal_name: str
    food_name: str
    calories: float
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None

    class Config:
        from_attributes = True


class DailySummary(BaseModel):
    date: date
    total_calories: float = 0.0
    total_protein_g: float = 0.0
    total_carbs_g: float = 0.0
    total_fat_g: float = 0.0
    logs: List[NutritionLogResponse] = []
    meals: Dict[str, List[NutritionLogResponse]] = {}
