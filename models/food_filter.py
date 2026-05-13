from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from database import Base


class FoodFilter(Base):
    __tablename__ = "food_filters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), nullable=False)
    slug = Column(String(80), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)

    default_query = Column(String(120), nullable=True)

    include_keywords = Column(JSON, nullable=False, default=list)
    exclude_keywords = Column(JSON, nullable=False, default=list)

    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
