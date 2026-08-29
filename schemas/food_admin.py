from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


# ──────────────────────────────────────────────
# Food Category
# ──────────────────────────────────────────────

class FoodCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)


class FoodCategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)


class FoodCategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Micronutrient
# ──────────────────────────────────────────────

class MicronutrientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    unit: str = Field(..., min_length=1, max_length=20)
    rda: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    category: Optional[str] = Field(None, max_length=50)


class MicronutrientUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    unit: Optional[str] = Field(None, min_length=1, max_length=20)
    rda: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    category: Optional[str] = Field(None, max_length=50)


class MicronutrientResponse(BaseModel):
    id: int
    name: str
    unit: str
    rda: Optional[float] = None
    category: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Food Micronutrient
# ──────────────────────────────────────────────

class FoodMicronutrientCreate(BaseModel):
    micronutrient_id: int = Field(..., ge=1)
    amount: float = Field(..., ge=0, allow_inf_nan=False)
    unit: str = Field(..., min_length=1, max_length=20)


class FoodMicronutrientUpdate(BaseModel):
    amount: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    unit: Optional[str] = Field(None, min_length=1, max_length=20)


class FoodMicronutrientResponse(BaseModel):
    id: int
    food_id: int
    micronutrient_id: int
    amount: float
    unit: str
    micronutrient: Optional[MicronutrientResponse] = None

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Food
# ──────────────────────────────────────────────

class FoodCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    brand: Optional[str] = Field(None, max_length=100)
    category_id: Optional[int] = Field(None, ge=1)
    calories: float = Field(..., ge=0, le=10000, allow_inf_nan=False)
    protein_g: float = Field(0.0, ge=0, le=1000, allow_inf_nan=False)
    carbs_g: float = Field(0.0, ge=0, le=1000, allow_inf_nan=False)
    fat_g: float = Field(0.0, ge=0, le=1000, allow_inf_nan=False)
    serving_size_g: float = Field(100.0, gt=0, le=100000, allow_inf_nan=False)
    barcode: Optional[str] = Field(None, max_length=50)
    fdc_id: Optional[int] = Field(None, ge=1)
    is_active: bool = True


class FoodUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    brand: Optional[str] = Field(None, max_length=100)
    category_id: Optional[int] = Field(None, ge=1)
    calories: Optional[float] = Field(None, ge=0, le=10000, allow_inf_nan=False)
    protein_g: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    carbs_g: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    fat_g: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    serving_size_g: Optional[float] = Field(None, gt=0, le=100000, allow_inf_nan=False)
    barcode: Optional[str] = Field(None, max_length=50)
    fdc_id: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None


class FoodResponse(BaseModel):
    id: int
    name: str
    brand: Optional[str] = None
    category_id: Optional[int] = None
    category: Optional[FoodCategoryResponse] = None
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    serving_size_g: float
    barcode: Optional[str] = None
    fdc_id: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
