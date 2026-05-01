import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from cachetools import TTLCache
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.food import Food
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


def _parse_food_item(item: dict) -> dict:
    """Parse a USDA food item into a clean response dict."""
    nutrients = item.get("foodNutrients", [])
    return {
        "fdc_id": item.get("fdcId"),
        "name": item.get("description"),
        "brand": item.get("brandOwner"),
        "calories": _extract_nutrient(nutrients, "Energy"),
        "protein_g": _extract_nutrient(nutrients, "Protein"),
        "carbs_g": _extract_nutrient(nutrients, "Carbohydrate, by difference"),
        "fat_g": _extract_nutrient(nutrients, "Total lipid (fat)"),
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


@router.get("/search")
def search_food(
    q: str = Query(..., min_length=2, description="Search term"),
    limit: int = Query(10, ge=1, le=25, description="Max results"),
    current_user: User = Depends(get_current_user),
):
    """Search the USDA FoodData Central database.

    Returns a list of food items with basic nutritional info (calories,
    protein, carbs, fat). Results are cached for 1 hour.
    """
    _check_api_key()

    cache_key = f"{q.lower()}:{limit}"

    # Return cached results if available
    if cache_key in _search_cache:
        return _search_cache[cache_key]

    try:
        response = httpx.get(
            f"{USDA_BASE_URL}/foods/search",
            params={"query": q, "pageSize": limit, "api_key": USDA_API_KEY},
            timeout=10.0,
        )
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Non-200 status: {response.status_code}",
                request=response.request,
                response=response,
            )

        data = response.json()
        results = [_parse_food_item(item) for item in data.get("foods", [])]

        _search_cache[cache_key] = results
        _stale_search_cache[cache_key] = results

        return results

    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
        if cache_key in _stale_search_cache:
            return _stale_search_cache[cache_key]

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="USDA food search service is temporarily unavailable. Please try again later.",
        )


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


@router.get("/local")
def search_local_food(
    search: str = Query("", description="Search term for local admin-managed foods"),
    limit: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Search local admin-managed foods by name or brand.
    Returns active foods only. Does not call USDA.
    """
    query = db.query(Food).filter(Food.is_active == True)
    if search:
        pattern = f"%{search}%"
        query = query.filter((Food.name.ilike(pattern)) | (Food.brand.ilike(pattern)))
    foods = query.order_by(Food.name.asc()).limit(limit).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "brand": f.brand,
            "calories": f.calories,
            "protein_g": f.protein_g,
            "carbs_g": f.carbs_g,
            "fat_g": f.fat_g,
            "serving_size_g": f.serving_size_g,
            "source": "local",
        }
        for f in foods
    ]
