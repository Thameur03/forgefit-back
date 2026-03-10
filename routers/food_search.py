import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from cachetools import TTLCache
from dotenv import load_dotenv

from models.user import User
from auth.utils import get_current_user

load_dotenv()

router = APIRouter()

USDA_API_KEY = os.getenv("USDA_API_KEY")
USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

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
