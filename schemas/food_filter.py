import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def slugify(value: str) -> str:
    """Generate a URL-friendly slug from a display name."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


class FoodFilterBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    slug: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=1000)
    default_query: Optional[str] = Field(None, max_length=120)
    include_keywords: List[str] = Field(default_factory=list, max_length=50)
    exclude_keywords: List[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True
    sort_order: int = Field(0, ge=0, le=1000)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError(
                "Slug must contain lowercase letters, numbers, and hyphens only"
            )
        return v

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def clean_keywords(cls, v):
        cleaned = [item.strip().lower() for item in v if item and item.strip()]
        if any(len(item) > 80 for item in cleaned):
            raise ValueError("Keywords cannot exceed 80 characters")
        return cleaned


class FoodFilterCreate(FoodFilterBase):
    pass


class FoodFilterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    slug: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=1000)
    default_query: Optional[str] = Field(None, max_length=120)
    include_keywords: Optional[List[str]] = Field(None, max_length=50)
    exclude_keywords: Optional[List[str]] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=1000)


class FoodFilterResponse(FoodFilterBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
