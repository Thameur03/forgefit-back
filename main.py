from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from routers.public import router as public_router

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
import models.account_deletion
import models.admin_audit
import models.operational_event
import models.lab_insights
import logging

from limiter import limiter
from brand import API_DESCRIPTION, API_TITLE
from config import (
    auto_create_tables_enabled,
    get_app_env,
    is_production,
    parse_cors_origins,
    require_email_verification,
)
from services.operational_counters import increment as increment_counter

logger = logging.getLogger(__name__)


APP_ENV = get_app_env()
AUTO_CREATE_TABLES = auto_create_tables_enabled()
PRODUCTION = is_production()


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
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)

app.state.limiter = limiter


async def handle_rate_limit(request: Request, exc: RateLimitExceeded):
    if request.url.path.startswith("/analytics/"):
        increment_counter("analytics_ingest_rejected")
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, handle_rate_limit)


@app.exception_handler(RequestValidationError)
async def handle_request_validation(request: Request, exc: RequestValidationError):
    if request.url.path in {
        "/analytics/events",
        "/analytics/events/public",
        "/analytics/identity/link",
    }:
        increment_counter("analytics_ingest_rejected")
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(IntegrityError)
async def handle_integrity_error(_: Request, __: IntegrityError):
    """Never expose raw database constraint details to API clients."""
    return JSONResponse(
        status_code=409,
        content={"detail": "The requested change conflicts with existing data"},
    )


class AnalyticsPayloadLimitMiddleware:
    """Bound analytics request bodies even when clients use chunked transfer."""

    _paths = {
        "/analytics/events",
        "/analytics/events/public",
        "/analytics/identity/link",
    }
    _max_bytes = 16 * 1024

    def __init__(self, app):
        self.app = app

    async def _reject(self, scope, receive, send, status_code: int, detail: str):
        increment_counter("analytics_ingest_rejected")
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self._paths:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._reject(
                    scope,
                    receive,
                    send,
                    400,
                    "Invalid Content-Length",
                )
                return
            if content_length > self._max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    413,
                    "Analytics payload too large",
                )
                return

        messages = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.disconnect":
                break
            if message.get("type") != "http.request":
                continue
            received += len(message.get("body", b""))
            if received > self._max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    413,
                    "Analytics payload too large",
                )
                return
            if not message.get("more_body", False):
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


app.add_middleware(AnalyticsPayloadLimitMiddleware)

cors_origins = parse_cors_origins(app_env=APP_ENV)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
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
logger.info(
    "[Startup] environment=%s email_verification=%s cors_origins=%d",
    APP_ENV,
    require_email_verification(),
    len(cors_origins),
)


# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(account_router, prefix="/account", tags=["Account"])
app.include_router(public_router)
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
    return {"status": "ok"}
