from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AnalyticsDateRange(BaseModel):
    start_date: date
    end_date: date
    timezone: str = "UTC"


class MetricValue(BaseModel):
    value: float
    previous_value: Optional[float] = None
    change_percent: Optional[float] = None
    unit: str = "count"
    trustworthy: bool = True
    limitation: Optional[str] = None


class ExecutiveOverviewResponse(BaseModel):
    date_range: AnalyticsDateRange
    metrics: dict[str, MetricValue]
    data_quality: list[str]


class GrowthPoint(BaseModel):
    date: date
    new_users: int
    verified_new_users: int
    cumulative_users: int


class GrowthResponse(BaseModel):
    date_range: AnalyticsDateRange
    points: list[GrowthPoint]
    total_new_users: int
    previous_period_new_users: Optional[int] = None
    change_percent: Optional[float] = None


class FunnelStage(BaseModel):
    key: str
    label: str
    count: int
    eligible_count: Optional[int] = None
    conversion_from_previous: Optional[float] = None
    conversion_from_start: Optional[float] = None
    drop_off_percent: Optional[float] = None


class SignupFunnelV2Response(BaseModel):
    date_range: AnalyticsDateRange
    stages: list[FunnelStage]
    identity_semantics: str
    data_quality: list[str]


class ActivityPoint(BaseModel):
    date: date
    dau: int
    wau: int
    mau: int


class RetentionMetric(BaseModel):
    day: int
    eligible_users: int
    retained_users: int
    rate: Optional[float] = None


class CohortRetentionRow(BaseModel):
    cohort_week: date
    cohort_size: int
    d1: RetentionMetric
    d7: RetentionMetric
    d14: RetentionMetric
    d30: RetentionMetric


class RetentionV2Response(BaseModel):
    date_range: AnalyticsDateRange
    dau: int
    wau: int
    mau: int
    dau_mau_stickiness: float
    wau_mau_stickiness: float
    activity_series: list[ActivityPoint]
    summary: list[RetentionMetric]
    cohorts: list[CohortRetentionRow]
    semantics: str


class FeatureAdoptionItem(BaseModel):
    key: str
    label: str
    users: int
    active_user_percentage: Optional[float] = None
    source: str
    limitation: Optional[str] = None


class CoreFeatureSplit(BaseModel):
    workout_only: int
    nutrition_only: int
    workout_and_nutrition: int
    neither_core: int


class FeatureAdoptionResponse(BaseModel):
    date_range: AnalyticsDateRange
    active_users: int
    features: list[FeatureAdoptionItem]
    core_feature_split: CoreFeatureSplit
    data_quality: list[str]


class TimeSeriesPoint(BaseModel):
    date: date
    count: int
    unique_users: int = 0


class NamedCount(BaseModel):
    key: str
    label: str
    count: int
    unique_users: Optional[int] = None


class WorkoutAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    completed_workouts: int
    unique_workout_users: int
    workouts_per_active_user: float
    average_workouts_per_user_week: float
    average_duration_minutes: Optional[float] = None
    duration_sample_size: int
    total_sets: int
    total_training_volume_kg: float
    scheduled_workouts_matched: int
    unscheduled_workouts: int
    scheduled_completion_rate: Optional[float] = None
    personal_records: int
    series: list[TimeSeriesPoint]
    top_exercises: list[NamedCount]
    data_quality: list[str]


class NutritionAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    nutrition_entries: int
    meals_logged: int
    unique_nutrition_users: int
    nutrition_logging_days: int
    average_logging_days_per_active_user: float
    barcode_uses: int
    manual_food_adds: int
    food_searches: int
    food_search_failures: int
    macro_goal_users: int
    micronutrient_users: int
    series: list[TimeSeriesPoint]
    top_catalog_foods: list[NamedCount]
    data_quality: list[str]


class ProgramAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    template_activations: int
    active_programs: int
    users_with_active_program: int
    users_without_active_program: int
    custom_programs: int
    template_programs: int
    program_changes: int
    most_used_templates: list[NamedCount]
    data_quality: list[str]


class SchedulingAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    scheduled_workouts: int
    unique_scheduling_users: int
    completed_events: int
    cancelled_events: int
    scheduled_to_completed_rate: Optional[float] = None
    upcoming_scheduled_count: int
    series: list[TimeSeriesPoint]
    data_quality: list[str]


class InsightsAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    eligible_users: int
    eligible_users_with_core_history: int
    users_lacking_core_history: int
    lab_insights_users: int
    lab_insights_views: int
    insights_generated: int
    insights_refreshed: int = 0
    insight_impressions: int = 0
    insight_opens: int = 0
    evidence_expansions: int = 0
    action_opens: int = 0
    generation_failures: int = 0
    average_views_per_viewer: float
    recommendation_interactions: int
    common_categories: list[NamedCount]
    data_quality: list[str]


class AnalyticsEventItem(BaseModel):
    id: int
    event_name: str
    user_id: Optional[int] = None
    actor: str
    occurred_at: datetime
    platform: Optional[str] = None
    app_version: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AnalyticsEventPage(BaseModel):
    items: list[AnalyticsEventItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class ErrorAnalyticsItem(BaseModel):
    event_name: str
    error_code: Optional[str] = None
    count: int
    unique_users: int
    last_occurred: datetime


class ErrorAnalyticsResponse(BaseModel):
    date_range: AnalyticsDateRange
    items: list[ErrorAnalyticsItem]
