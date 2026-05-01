from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


# ──────────────────────────────────────────────
# Food Category
# ──────────────────────────────────────────────

class FoodCategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None


class FoodCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


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
    name: str
    unit: str
    rda: Optional[float] = None
    category: Optional[str] = None


class MicronutrientUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    rda: Optional[float] = None
    category: Optional[str] = None


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
    micronutrient_id: int
    amount: float
    unit: str


class FoodMicronutrientUpdate(BaseModel):
    amount: Optional[float] = None
    unit: Optional[str] = None


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
    name: str
    brand: Optional[str] = None
    category_id: Optional[int] = None
    calories: float
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    serving_size_g: float = 100.0
    barcode: Optional[str] = None
    fdc_id: Optional[int] = None
    is_active: bool = True


class FoodUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category_id: Optional[int] = None
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    serving_size_g: Optional[float] = None
    barcode: Optional[str] = None
    fdc_id: Optional[int] = None
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
