from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class LabContextUpdate(BaseModel):
    timezone: Optional[str] = Field(default=None, max_length=64)
    canonical_goal: Optional[str] = Field(default=None, max_length=64)


class LabContextResponse(LabContextUpdate):
    pass


class InsightImpressions(BaseModel):
    insight_ids: list[str] = Field(max_length=20)


class LabInsightsV2Response(BaseModel):
    schema_version: Literal["2.0"]
    analytics_version: str
    analysis_id: str
    generated_at: datetime
    data_through: datetime
    stale_after: datetime
    is_stale: bool
    cache_status: Literal["generated", "hit", "stale_fallback"]
    source_data_watermark: str
    user_timezone: str
    period: dict[str, Any]
    domain_coverage: list[dict[str, Any]]
    metrics: dict[str, Any]
    insights: list[dict[str, Any]]
    resolved_insights: list[dict[str, Any]]
    limitations: list[str]
    disclaimer: str
