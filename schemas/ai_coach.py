from datetime import date, datetime
from typing import List, Literal, Optional
from pydantic import BaseModel


Priority = Literal["high", "medium", "low"]
Category = Literal["workout", "nutrition", "recovery"]
Confidence = Literal["high", "medium", "low"]
ReadinessLabel = Literal["Excellent", "Good", "Moderate", "Needs Attention"]
# Describes how much data was available for this analysis period
DataState = Literal["no_data", "workout_only", "nutrition_only", "limited", "sufficient"]


class UnlockStatusResponse(BaseModel):
    unlocked: bool
    days_remaining: int
    unlock_date: date
    created_at: date



class AICoachRecommendation(BaseModel):
    title: str
    reason: str
    action: str
    priority: Priority
    category: Category
    impact: int
    metric: Optional[str] = None


class AICoachWarning(BaseModel):
    code: str
    title: str
    detail: str
    priority: Priority = "medium"


class AICoachScoreBreakdown(BaseModel):
    no_workouts_deduction: float = 0.0
    adherence_deduction: float = 0.0
    volume_spike_deduction: float = 0.0
    volume_spike_training_deduction: float = 0.0
    muscle_imbalance_deduction: float = 0.0

    low_protein_deduction: float = 0.0
    low_logging_deduction: float = 0.0
    calorie_cv_deduction: float = 0.0

    missing_weight_note: bool = False
    missing_active_program_note: bool = False


class AICoachSummaryResponse(BaseModel):
    generated_at: datetime
    period_days: int

    overall_score: int
    training_score: int
    nutrition_score: int
    recovery_score: int
    readiness_label: ReadinessLabel

    confidence: Confidence
    confidence_reason: str
    missing_data: List[str] = []

    # Data availability state — used by frontend for honest presentation
    data_state: DataState = "sufficient"
    has_sufficient_data: bool = True

    summary: str
    recommendations: List[AICoachRecommendation] = []
    warnings: List[AICoachWarning] = []
    next_best_action: Optional[str] = None

    score_breakdown: AICoachScoreBreakdown

    workouts_this_period: int = 0
    workouts_previous_period: int = 0
    weekly_volume_kg: float = 0.0
    previous_weekly_volume_kg: float = 0.0
    volume_change_percent: Optional[float] = None

    active_program_name: Optional[str] = None
    active_program_days_per_week: Optional[int] = None
    adherence_percent: Optional[float] = None

    average_daily_calories: float = 0.0
    average_daily_protein_g: float = 0.0
    protein_per_kg: Optional[float] = None
    nutrition_logging_consistency_percent: float = 0.0
    calorie_coefficient_of_variation: Optional[float] = None

    disclaimer: str = (
        "This score reflects your training, nutrition, and logging patterns inside the app. "
        "It is not medical advice."
    )
