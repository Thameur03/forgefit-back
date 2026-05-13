from datetime import date
from typing import Optional, List

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


class MuscleVolumeResponse(BaseModel):
    muscle_group: str
    total_volume_kg: float
    total_sets: int
    percentage: float
    previous_volume_kg: float
    trend_percent: float


class MuscleVolumeListResponse(BaseModel):
    period_label: str
    items: List[MuscleVolumeResponse]


# ── Nutrition Dashboard ──────────────────────────────────────────────────────

class MacroSplitSchema(BaseModel):
    protein_percent: float = 0.0
    carbs_percent: float = 0.0
    fat_percent: float = 0.0


class CalorieConsistencySchema(BaseModel):
    standard_deviation: Optional[float] = None
    coefficient_of_variation: Optional[float] = None
    label: str = "Not enough data"


class NutritionDailyPoint(BaseModel):
    date: date
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0


class NutritionPeriodSummary(BaseModel):
    average_calories: float = 0.0
    average_protein_g: float = 0.0


class NutritionDashboardResponse(BaseModel):
    period_days: int = 14
    logged_days: int = 0
    logging_consistency_percent: float = 0.0

    average_calories: float = 0.0
    average_protein_g: float = 0.0
    average_carbs_g: float = 0.0
    average_fat_g: float = 0.0

    protein_per_kg: Optional[float] = None

    macro_split: MacroSplitSchema = MacroSplitSchema()

    current_period: NutritionPeriodSummary = NutritionPeriodSummary()
    previous_period: NutritionPeriodSummary = NutritionPeriodSummary()

    calorie_change_percent: float = 0.0
    protein_change_percent: float = 0.0

    calorie_consistency: CalorieConsistencySchema = CalorieConsistencySchema()

    daily_points: List[NutritionDailyPoint] = []
    insights: List[str] = []
