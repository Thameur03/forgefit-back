from sqlalchemy import Column, Integer, String, DateTime, Boolean, Date, Float
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Level 2 columns
    is_verified = Column(Boolean, default=False, nullable=False, server_default="false")
    verification_code = Column(String, nullable=True)
    verification_code_expires = Column(DateTime(timezone=True), nullable=True)
    reset_password_code = Column(String, nullable=True)
    reset_password_code_expires = Column(DateTime(timezone=True), nullable=True)

    # Profile fields
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(20), nullable=True)
    weight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)
    fitness_level = Column(String(20), nullable=True)