from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from database import engine, Base
from routers.auth import router as auth_router
from routers.workouts import router as workouts_router
from routers.exercises import router as exercises_router
from routers.nutrition import router as nutrition_router
from routers.food_search import router as food_search_router
from routers.stats import router as stats_router
from routers.programs import router as programs_router
from routers.admin import router as admin_router
from routers.admin_food_filters import router as admin_food_filters_router
from routers.schedule import router as schedule_router
import models.user
import models.workout
import models.nutrition
import models.token
import models.program
import models.admin
import models.food
import models.food_filter
import models.schedule
import os

from limiter import limiter

app = FastAPI(
    title="ForgeFit API",
    description="Backend for ForgeFit mobile app",
    version="1.0.0"
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
Base.metadata.create_all(bind=engine)


# ── Seed default food filters ────────────────────────────────────────────────

def _seed_food_filters():
    """Populate `food_filters` with sensible defaults if the table is empty."""
    from database import SessionLocal
    from models.food_filter import FoodFilter

    db = SessionLocal()
    try:
        if db.query(FoodFilter).count() > 0:
            return

        defaults = [
            FoodFilter(
                name="Fruit", slug="fruit",
                description="Fresh and packaged fruit foods",
                default_query="fruit",
                include_keywords=["apple", "banana", "orange", "berries", "strawberry", "blueberry", "fruit"],
                exclude_keywords=["candy", "soda", "flavor", "flavoured"],
                sort_order=1,
            ),
            FoodFilter(
                name="Meat", slug="meat",
                description="Meat and poultry foods",
                default_query="meat",
                include_keywords=["beef", "chicken", "turkey", "pork", "lamb", "meat"],
                exclude_keywords=["plant based", "vegan", "meatless"],
                sort_order=2,
            ),
            FoodFilter(
                name="Vegan-friendly", slug="vegan-friendly",
                description="Plant-based food search filter",
                default_query="plant based",
                include_keywords=["tofu", "lentils", "beans", "chickpeas", "vegetables", "fruit", "plant based"],
                exclude_keywords=["beef", "chicken", "pork", "fish", "milk", "cheese", "egg", "honey", "yogurt", "butter"],
                sort_order=3,
            ),
            FoodFilter(
                name="Dairy", slug="dairy",
                description="Dairy and dairy-based foods",
                default_query="dairy",
                include_keywords=["milk", "cheese", "yogurt", "dairy", "cream"],
                exclude_keywords=["dairy free", "plant based", "vegan"],
                sort_order=4,
            ),
            FoodFilter(
                name="High Protein", slug="high-protein",
                description="High-protein food search filter",
                default_query="high protein",
                include_keywords=["chicken", "beef", "tuna", "salmon", "egg", "greek yogurt", "protein"],
                exclude_keywords=[],
                sort_order=5,
            ),
        ]

        db.add_all(defaults)
        db.commit()
    finally:
        db.close()


_seed_food_filters()


# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(workouts_router, prefix="/workouts", tags=["Workouts"])
app.include_router(exercises_router, prefix="/exercises", tags=["Exercises"])
app.include_router(nutrition_router, prefix="/nutrition", tags=["Nutrition"])
app.include_router(food_search_router, prefix="/food", tags=["Food Search"])
app.include_router(stats_router, prefix="/stats", tags=["Statistics"])
app.include_router(programs_router, prefix="/programs", tags=["Programs"])
app.include_router(admin_router, prefix="/admin", tags=["Admin"])
app.include_router(admin_food_filters_router, prefix="/admin", tags=["Admin Food Filters"])
app.include_router(schedule_router, prefix="/schedule", tags=["Schedule"])


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "ForgeFit API"}

