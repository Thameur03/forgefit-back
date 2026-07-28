from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey
from database import Base

class NutritionLog(Base):
    __tablename__ = "nutrition_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    meal_name = Column(String, nullable=False)
    food_name = Column(String, nullable=False)
    calories = Column(Float, nullable=False)
    protein_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    fdc_id = Column(Integer, nullable=True)
    # Idempotency key sent by the Flutter client.
    # Nullable: legacy rows (client_request_id IS NULL) are unaffected.
    # Uniqueness enforced by a partial PostgreSQL index — see migration 004.
    client_request_id = Column(String(36), nullable=True, index=False)