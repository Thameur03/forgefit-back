import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from cachetools import TTLCache
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.food import Food
from models.food_filter import FoodFilter
from auth.utils import get_current_user

load_dotenv()

router = APIRouter()

USDA_API_KEY = os.getenv("USDA_API_KEY")
USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# Nutrients cache (24 hr)
_nutrients_cache: TTLCache = TTLCache(maxsize=500, ttl=86400)

# USDA nutrient ID → human-readable name, unit, and RDA
NUTRIENT_MAP = {
    1003: {"name": "Protein", "unit": "g", "rda": 50},
    1004: {"name": "Fat", "unit": "g", "rda": 65},
    1005: {"name": "Carbohydrates", "unit": "g", "rda": 300},
    1008: {"name": "Calories", "unit": "kcal", "rda": 2000},
    1079: {"name": "Fiber", "unit": "g", "rda": 28},
    1087: {"name": "Calcium", "unit": "mg", "rda": 1000},
    1089: {"name": "Iron", "unit": "mg", "rda": 18},
    1090: {"name": "Magnesium", "unit": "mg", "rda": 420},
    1091: {"name": "Phosphorus", "unit": "mg", "rda": 700},
    1092: {"name": "Potassium", "unit": "mg", "rda": 4700},
    1093: {"name": "Sodium", "unit": "mg", "rda": 2300},
    1095: {"name": "Zinc", "unit": "mg", "rda": 11},
    1098: {"name": "Copper", "unit": "mg", "rda": 0.9},
    1106: {"name": "Vitamin A", "unit": "µg", "rda": 900},
    1109: {"name": "Vitamin E", "unit": "mg", "rda": 15},
    1114: {"name": "Vitamin D", "unit": "µg", "rda": 20},
    1162: {"name": "Vitamin C", "unit": "mg", "rda": 90},
    1165: {"name": "Thiamin (B1)", "unit": "mg", "rda": 1.2},
    1166: {"name": "Riboflavin (B2)", "unit": "mg", "rda": 1.3},
    1167: {"name": "Niacin (B3)", "unit": "mg", "rda": 16},
    1175: {"name": "Vitamin B6", "unit": "mg", "rda": 1.7},
    1177: {"name": "Folate (B9)", "unit": "µg", "rda": 400},
    1178: {"name": "Vitamin B12", "unit": "µg", "rda": 2.4},
    1185: {"name": "Vitamin K", "unit": "µg", "rda": 120},
}

# Search-results cache (1 hr)
_search_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)
_stale_search_cache: dict[str, list] = {}

# Single food-detail cache (24 hr)
_detail_cache: TTLCache = TTLCache(maxsize=500, ttl=86400)


def _extract_nutrient(nutrients: list, name: str) -> float:
    """Extract a nutrient value from the USDA foodNutrients list.

    Returns 0.0 if the nutrient is not found.
    """
    for n in nutrients:
        if n.get("nutrientName") == name:
            return float(n.get("value", 0.0))
    return 0.0


import logging as _logging
_food_logger = _logging.getLogger(__name__)


def _serving_size_g(item: dict) -> float | None:
    """Return the serving size in grams for a USDA food item, or None.

    USDA Branded Foods embed servingSize / servingSizeUnit.
    Foundation / SR-Legacy / Survey foods have NO servingSize — their
    nutrient values are already expressed per 100 g.
    """
    size = item.get("servingSize")
    unit = (item.get("servingSizeUnit") or "").upper()
    if size and size > 0:
        if unit in ("G", "GRM", "GRAMS", ""):
            return float(size)
        if unit in ("ML", "MLS", "MILLILITER", "MILLILITERS"):
            # Approximate: 1 ml ≈ 1 g for most foods
            return float(size)
        if unit in ("OZ", "OZA"):
            return float(size) * 28.3495
        # Unknown unit — fall through and treat as grams (best effort)
        return float(size)
    return None


def _parse_food_item(item: dict) -> dict:
    """Parse a USDA food item into a clean response dict.

    All macro/calorie values are normalised to **per 100 g** so that the
    Flutter client can uniformly apply:   displayed = value * (selectedGrams / 100)

    USDA Branded Foods return nutrient amounts per *serving*, so we divide
    by (servingSize / 100) to convert to per-100 g.
    Foundation / SR-Legacy foods have no servingSize and are already per 100 g.
    """
    nutrients = item.get("foodNutrients", [])

    raw_cal   = _extract_nutrient(nutrients, "Energy")
    raw_pro   = _extract_nutrient(nutrients, "Protein")
    raw_carbs = _extract_nutrient(nutrients, "Carbohydrate, by difference")
    raw_fat   = _extract_nutrient(nutrients, "Total lipid (fat)")

    serving_g = _serving_size_g(item)
    name = item.get("description", "")

    if serving_g and serving_g > 0 and abs(serving_g - 100.0) > 1.0:
        # Nutrient values are per serving — convert to per 100 g
        factor = 100.0 / serving_g
        calories  = round(raw_cal   * factor, 2)
        protein_g = round(raw_pro   * factor, 2)
        carbs_g   = round(raw_carbs * factor, 2)
        fat_g     = round(raw_fat   * factor, 2)
    else:
        # Already per 100 g (or no serving size info)
        calories  = raw_cal
        protein_g = raw_pro
        carbs_g   = raw_carbs
        fat_g     = raw_fat

    # Sanity warnings — per-100g values should not exceed these
    if calories > 1000:
        _food_logger.warning("Suspicious calories per 100g for '%s': %.1f (serving_g=%s)", name, calories, serving_g)
    if protein_g > 100:
        _food_logger.warning("Suspicious protein per 100g for '%s': %.1f", name, protein_g)
    if carbs_g > 100:
        _food_logger.warning("Suspicious carbs per 100g for '%s': %.1f", name, carbs_g)
    if fat_g > 100:
        _food_logger.warning("Suspicious fat per 100g for '%s': %.1f", name, fat_g)

    return {
        "fdc_id":     item.get("fdcId"),
        "name":       name,
        "brand":      item.get("brandOwner"),
        "calories":   calories,
        "protein_g":  protein_g,
        "carbs_g":    carbs_g,
        "fat_g":      fat_g,
        # Always 100 — Flutter uses this as the base weight
        "serving_size_g": 100,
    }


def _check_api_key() -> None:
    """Raise a helpful error if USDA_API_KEY is not configured."""
    if not USDA_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "USDA_API_KEY is not configured. "
                "Get a free key at https://fdc.nal.usda.gov/api-key-signup.html "
                "and add USDA_API_KEY=<your_key> to .env"
            ),
        )


# ── Filter helpers ───────────────────────────────────────────────────────────


def _food_matches_filter(food: dict, food_filter: FoodFilter) -> bool:
    """Return True if *food* passes the filter's keyword rules."""
    text_parts = [
        food.get("name") or "",
        food.get("brand") or "",
        food.get("brand_name") or "",
        food.get("category") or "",
        food.get("food_category") or "",
        food.get("description") or "",
    ]
    text = " ".join(text_parts).lower()

    exclude = food_filter.exclude_keywords or []
    include = food_filter.include_keywords or []

    if any(kw.lower() in text for kw in exclude):
        return False

    if include:
        return any(kw.lower() in text for kw in include)

    return True


def _food_filter_score(food: dict, food_filter: FoodFilter, query: str) -> int:
    """Higher score → better match for the filter."""
    text_parts = [
        food.get("name") or "",
        food.get("brand") or "",
        food.get("category") or "",
    ]
    text = " ".join(text_parts).lower()
    score = 0
    if query and query.lower() in text:
        score += 10
    for kw in food_filter.include_keywords or []:
        if kw.lower() in text:
            score += 3
    for kw in food_filter.exclude_keywords or []:
        if kw.lower() in text:
            score -= 100
    return score


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/filters")
def get_food_filters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return active food filters ordered by sort_order then name."""
    return (
        db.query(FoodFilter)
        .filter(FoodFilter.is_active == True)
        .order_by(FoodFilter.sort_order.asc(), FoodFilter.name.asc())
        .all()
    )


@router.get("/search")
def search_food(
    q: Optional[str] = Query(None, min_length=2, description="Search term"),
    limit: int = Query(10, ge=1, le=25, description="Max results"),
    filter: Optional[str] = Query(None, description="Filter slug"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search the USDA FoodData Central database.

    Optionally apply an admin-managed food filter by slug.
    If *q* is empty and a filter is selected, the filter's ``default_query``
    is used automatically.
    """
    _check_api_key()

    food_filter = None
    if filter:
        food_filter = (
            db.query(FoodFilter)
            .filter(FoodFilter.slug == filter, FoodFilter.is_active == True)
            .first()
        )
        if not food_filter:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Food filter not found",
            )

    # Resolve effective query
    effective_q = (q or "").strip()
    if not effective_q and food_filter:
        effective_q = (food_filter.default_query or "").strip()
    if not effective_q and food_filter and food_filter.include_keywords:
        effective_q = food_filter.include_keywords[0]
    if not effective_q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query required",
        )

    # Fetch more from USDA when filtering so post-filter has enough results
    usda_limit = limit * 5 if food_filter else limit
    cache_key = f"{effective_q.lower()}:{usda_limit}"

    # Return cached results if available (pre-filter cache)
    cached = _search_cache.get(cache_key)
    if cached is None:
        try:
            response = httpx.get(
                f"{USDA_BASE_URL}/foods/search",
                params={
                    "query": effective_q,
                    "pageSize": usda_limit,
                    "api_key": USDA_API_KEY,
                },
                timeout=10.0,
            )
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"Non-200 status: {response.status_code}",
                    request=response.request,
                    response=response,
                )

            data = response.json()
            cached = [_parse_food_item(item) for item in data.get("foods", [])]

            _search_cache[cache_key] = cached
            _stale_search_cache[cache_key] = cached

        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
            cached = _stale_search_cache.get(cache_key)
            if cached is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="USDA food search service is temporarily unavailable. Please try again later.",
                )

    # Apply filter if present
    if food_filter:
        filtered = [f for f in cached if _food_matches_filter(f, food_filter)]
        filtered.sort(
            key=lambda f: _food_filter_score(f, food_filter, effective_q),
            reverse=True,
        )
        return filtered[:limit]

    return cached[:limit]


@router.get("/local")
def search_local_food(
    search: str = Query("", description="Search term for local admin-managed foods"),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search active, admin-managed local foods without calling USDA."""
    query = db.query(Food).filter(Food.is_active == True)
    if search:
        pattern = f"%{search}%"
        query = query.filter((Food.name.ilike(pattern)) | (Food.brand.ilike(pattern)))
    foods = query.order_by(Food.name.asc()).limit(limit).all()
    return [
        {
            "id": food.id,
            "name": food.name,
            "brand": food.brand,
            "calories": food.calories,
            "protein_g": food.protein_g,
            "carbs_g": food.carbs_g,
            "fat_g": food.fat_g,
            "serving_size_g": food.serving_size_g,
            "source": "local",
        }
        for food in foods
    ]


@router.get("/{fdc_id}")
def get_food_detail(
    fdc_id: int,
    current_user: User = Depends(get_current_user),
):
    """Get detailed nutritional info for a single USDA food item.

    Returns the food's macros and serving size info if available.
    Results are cached for 24 hours.
    """
    _check_api_key()

    if fdc_id in _detail_cache:
        return _detail_cache[fdc_id]

    try:
        response = httpx.get(
            f"{USDA_BASE_URL}/food/{fdc_id}",
            params={"api_key": USDA_API_KEY},
            timeout=10.0,
        )
        if response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Food item with fdc_id {fdc_id} not found",
            )
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Non-200 status: {response.status_code}",
                request=response.request,
                response=response,
            )

        item = response.json()
        result = _parse_food_item(item)

        # Add serving size info when available
        result["serving_size"] = item.get("servingSize")
        result["serving_size_unit"] = item.get("servingSizeUnit")
        result["household_serving_text"] = item.get("householdServingFullText")

        _detail_cache[fdc_id] = result
        return result

    except HTTPException:
        raise
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="USDA food service is temporarily unavailable. Please try again later.",
        )

@router.get("/{fdc_id}/nutrients")
async def get_food_nutrients(
    fdc_id: int,
    current_user: User = Depends(get_current_user),
):
    """Fetch full nutrient breakdown from USDA for a specific food.

    Returns a list of nutrients with amount, unit, RDA, and pct_rda,
    filtered to the nutrients mapped in NUTRIENT_MAP. Cached 24 hours.
    """
    _check_api_key()

    if fdc_id in _nutrients_cache:
        return _nutrients_cache[fdc_id]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{USDA_BASE_URL}/food/{fdc_id}",
                params={"api_key": USDA_API_KEY},
            )

        if response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Food item with fdc_id {fdc_id} not found",
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="USDA food service is temporarily unavailable.",
            )

        data = response.json()
        nutrients_raw = data.get("foodNutrients", [])

        result = []
        for n in nutrients_raw:
            nutrient_id = n.get("nutrient", {}).get("id")
            if nutrient_id in NUTRIENT_MAP:
                amount = n.get("amount", 0) or 0
                info = NUTRIENT_MAP[nutrient_id]
                result.append({
                    "id": nutrient_id,
                    "name": info["name"],
                    "amount": round(float(amount), 2),
                    "unit": info["unit"],
                    "rda": info["rda"],
                    "pct_rda": round((float(amount) / info["rda"]) * 100, 1)
                    if info["rda"] > 0 else 0,
                })

        # Sort by nutrient ID for consistent ordering
        result.sort(key=lambda x: x["id"])
        _nutrients_cache[fdc_id] = result
        return result

    except HTTPException:
        raise
    except (httpx.TimeoutException, httpx.ConnectError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="USDA food service is temporarily unavailable. Please try again later.",
        )
