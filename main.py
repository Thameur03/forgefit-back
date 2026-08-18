from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
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
from routers.admin_analytics import router as admin_analytics_router
from routers.analytics import router as analytics_router
from routers.schedule import router as schedule_router
from routers.ai import router as ai_router
from routers.account import router as account_router

import models.user
import models.workout
import models.nutrition
import models.token
import models.program
import models.admin
import models.food
import models.food_filter
import models.schedule
import models.analytics_event
import os
import logging

from limiter import limiter
from brand import API_DESCRIPTION, API_TITLE

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Kept enabled by default for deployment compatibility. Production operators
# that run Alembic before startup should explicitly set this to false.
AUTO_CREATE_TABLES = _env_flag("AUTO_CREATE_TABLES", default=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize schema compatibility and idempotent default data at startup."""
    if AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
    else:
        logger.info("[Database] Automatic table creation disabled")
    _seed_food_filters()
    yield


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
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

# ── Seed default food filters ────────────────────────────────────────────────

def _seed_food_filters():
    """Insert any missing default food filters without duplicating slugs."""
    from database import SessionLocal
    from models.food_filter import FoodFilter

    db = SessionLocal()
    try:
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

        existing_slugs = {
            slug for (slug,) in db.query(FoodFilter.slug).all()
        }
        missing = [item for item in defaults if item.slug not in existing_slugs]
        if not missing:
            return

        db.add_all(missing)
        try:
            db.commit()
        except IntegrityError:
            # Another startup worker may have inserted the same unique slugs.
            db.rollback()
            persisted_slugs = {
                slug for (slug,) in db.query(FoodFilter.slug).all()
            }
            expected_slugs = {item.slug for item in defaults}
            if not expected_slugs.issubset(persisted_slugs):
                # This was not the expected duplicate-slug race. Surface the
                # original integrity failure so startup cannot mask bad data or
                # schema drift.
                raise
            logger.info("[Database] Default food filters seeded concurrently")
        except Exception:
            db.rollback()
            raise
    finally:
        db.close()

# ── Startup config log ────────────────────────────────────────────────────────
_require_email_verification = os.getenv("REQUIRE_EMAIL_VERIFICATION", "true")
logger.info("[Auth] REQUIRE_EMAIL_VERIFICATION=%s", _require_email_verification)


# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(account_router, prefix="/account", tags=["Account"])
app.include_router(workouts_router, prefix="/workouts", tags=["Workouts"])

app.include_router(exercises_router, prefix="/exercises", tags=["Exercises"])
app.include_router(nutrition_router, prefix="/nutrition", tags=["Nutrition"])
app.include_router(food_search_router, prefix="/food", tags=["Food Search"])
app.include_router(stats_router, prefix="/stats", tags=["Statistics"])
app.include_router(programs_router, prefix="/programs", tags=["Programs"])
app.include_router(admin_router, prefix="/admin", tags=["Admin"])
app.include_router(admin_food_filters_router, prefix="/admin", tags=["Admin Food Filters"])
app.include_router(admin_analytics_router, prefix="/admin", tags=["Admin Analytics"])
app.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
app.include_router(schedule_router, prefix="/schedule", tags=["Schedule"])
app.include_router(ai_router, prefix="/ai", tags=["AI Coach"])


@app.get("/health")
def health_check():
    return {"status": "ok", "app": API_TITLE}
