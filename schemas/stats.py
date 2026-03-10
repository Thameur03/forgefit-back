from datetime import date
from typing import Optional

from pydantic import BaseModel


class WorkoutStats(BaseModel):
    total_workouts: int = 0
    total_sets: int = 0
    total_volume_kg: float = 0.0
    avg_workouts_per_week: float = 0.0
    most_trained_muscle: Optional[str] = None
    most_frequent_exercise: Optional[str] = None
    current_streak_days: int = 0
    longest_streak_days: int = 0


class NutritionStats(BaseModel):
    avg_daily_calories: float = 0.0
    avg_daily_protein_g: float = 0.0
    avg_daily_carbs_g: float = 0.0
    avg_daily_fat_g: float = 0.0
    days_logged: int = 0
    best_day_calories: float = 0.0


class PersonalRecord(BaseModel):
    exercise_name: str
    max_weight_kg: float
    max_reps: int
    date_achieved: date

    class Config:
        from_attributes = True


class WeeklyWorkoutData(BaseModel):
    week_start: date
    workout_count: int = 0
    total_volume_kg: float = 0.0


class DailyNutritionData(BaseModel):
    date: date
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
