from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routers.auth import router as auth_router
from routers.workouts import router as workouts_router
from routers.exercises import router as exercises_router
from routers.nutrition import router as nutrition_router
from routers.food_search import router as food_search_router
from routers.stats import router as stats_router
from routers.programs import router as programs_router
import models.user
import models.workout
import models.nutrition
import models.token
import models.program
import os

app = FastAPI(
    title="ForgeFit API",
    description="Backend for ForgeFit mobile app",
    version="1.0.0"
)

# CORS middleware — origins configurable via CORS_ORIGINS env variable
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
cors_origins = [origin.strip() for origin in cors_origins]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auto-create tables in development only; use Alembic migrations in production
from sqlalchemy import text

Base.metadata.create_all(bind=engine)

with engine.connect() as conn:
    conn.execute(text("""
      ALTER TABLE workouts 
      ADD COLUMN IF NOT EXISTS 
        name VARCHAR(255)
    """))
    conn.execute(text("""
      ALTER TABLE workouts 
      ADD COLUMN IF NOT EXISTS 
        duration_seconds INTEGER DEFAULT 0
    """))
    conn.execute(text("""
      ALTER TABLE users
      ADD COLUMN IF NOT EXISTS
        date_of_birth DATE
    """))
    conn.execute(text("""
      ALTER TABLE users
      ADD COLUMN IF NOT EXISTS
        gender VARCHAR(20)
    """))
    conn.execute(text("""
      ALTER TABLE users
      ADD COLUMN IF NOT EXISTS
        weight_kg FLOAT
    """))
    conn.execute(text("""
      ALTER TABLE users
      ADD COLUMN IF NOT EXISTS
        height_cm FLOAT
    """))
    conn.execute(text("""
      ALTER TABLE users
      ADD COLUMN IF NOT EXISTS
        fitness_level VARCHAR(20)
    """))
    conn.commit()

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(workouts_router, prefix="/workouts", tags=["Workouts"])
app.include_router(exercises_router, prefix="/exercises", tags=["Exercises"])
app.include_router(nutrition_router, prefix="/nutrition", tags=["Nutrition"])
app.include_router(food_search_router, prefix="/food", tags=["Food Search"])
app.include_router(stats_router, prefix="/stats", tags=["Statistics"])
app.include_router(programs_router, prefix="/programs", tags=["Programs"])


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "ForgeFit API"}
