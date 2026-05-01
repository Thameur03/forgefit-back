from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import DateTime
from database import Base


class FoodCategory(Base):
    __tablename__ = "food_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    foods = relationship("Food", back_populates="category")


class Food(Base):
    __tablename__ = "foods"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    brand = Column(String(100), nullable=True)
    category_id = Column(Integer, ForeignKey("food_categories.id", ondelete="SET NULL"), nullable=True)
    calories = Column(Float, nullable=False)
    protein_g = Column(Float, nullable=False, default=0.0)
    carbs_g = Column(Float, nullable=False, default=0.0)
    fat_g = Column(Float, nullable=False, default=0.0)
    serving_size_g = Column(Float, nullable=False, default=100.0)
    barcode = Column(String(50), nullable=True)
    fdc_id = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    category = relationship("FoodCategory", back_populates="foods")
    micronutrients = relationship(
        "FoodMicronutrient",
        back_populates="food",
        cascade="all, delete-orphan",
    )


class Micronutrient(Base):
    __tablename__ = "micronutrients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    unit = Column(String(20), nullable=False)   # mg, mcg, IU, etc.
    rda = Column(Float, nullable=True)          # recommended daily allowance
    category = Column(String(50), nullable=True)  # vitamin, mineral, etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    food_links = relationship("FoodMicronutrient", back_populates="micronutrient")


class FoodMicronutrient(Base):
    __tablename__ = "food_micronutrients"

    id = Column(Integer, primary_key=True, index=True)
    food_id = Column(Integer, ForeignKey("foods.id", ondelete="CASCADE"), nullable=False)
    micronutrient_id = Column(Integer, ForeignKey("micronutrients.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Float, nullable=False)
    unit = Column(String(20), nullable=False)

    food = relationship("Food", back_populates="micronutrients")
    micronutrient = relationship("Micronutrient", back_populates="food_links")
