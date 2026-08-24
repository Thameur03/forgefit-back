import logging
import math
import os
import re
import unicodedata
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

_food_logger = logging.getLogger(__name__)

# Accepted FoodData Central nutrient amounts are normalized to 100 g. Branded
# rows are only accepted directly when their declared basis unit is grams;
# volume-based rows are rejected because their values are per 100 mL. Label
# nutrients are per serving and are only used when a trustworthy mass exists.
_MAX_KCAL_PER_100G = 1000.0
_MAX_MACRO_G_PER_100G = 100.5
_MAX_MACRO_TOTAL_G_PER_100G = 105.0

_ENERGY_IDS = (1008, 2047, 2048)
_ENERGY_KJ_ID = 1062
_MACRO_IDS = {
    "protein": 1003,
    "fat": 1004,
    "carbs": 1005,
}
_GRAM_SERVING_UNITS = {"G", "GRAM", "GRAMS", "GRM"}
_VOLUME_SERVING_UNITS = {
    "FL OZ",
    "FLOZ",
    "L",
    "LITER",
    "LITERS",
    "LITRE",
    "LITRES",
    "LTR",
    "ML",
    "MLS",
    "MLT",
    "MILLILITER",
    "MILLILITERS",
    "MILLILITRE",
    "MILLILITRES",
}

_GENERIC_DATA_TYPE_SCORE = {
    "Survey (FNDDS)": 150,
    "Foundation": 115,
    "SR Legacy": 105,
    "Experimental": 90,
    "Branded": 0,
}
_SIMPLE_FOOD_WORDS = {
    "fresh",
    "nfs",
    "plain",
    "raw",
    "unprepared",
    "whole",
}
_PREPARED_FOOD_WORDS = {
    "baked",
    "breaded",
    "broiled",
    "cake",
    "candied",
    "chip",
    "coated",
    "fried",
    "grilled",
    "nectar",
    "pickled",
    "pudding",
    "roasted",
    "rotisserie",
    "salad",
    "sandwich",
    "sauteed",
    "split",
    "stewed",
}
_CORPORATE_WORDS = {
    "the",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "co",
    "company",
    "corp",
    "corporation",
    "foods",
    "food",
}
_UNREQUESTED_MIXED_DRINK_WORDS = {"brandy", "rum", "vodka", "whiskey"}

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


def _as_finite_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nutrient_fields(entry: dict) -> tuple[int | None, str, str, float | None]:
    """Read both compact search and nested detail nutrient representations."""
    nested = entry.get("nutrient") or {}
    nutrient_id = entry.get("nutrientId")
    if nutrient_id is None:
        nutrient_id = nested.get("id")
    try:
        nutrient_id = int(nutrient_id) if nutrient_id is not None else None
    except (TypeError, ValueError):
        nutrient_id = None

    name = str(entry.get("nutrientName") or nested.get("name") or "")
    unit = str(entry.get("unitName") or nested.get("unitName") or "")
    raw_amount = entry.get("value") if "value" in entry else entry.get("amount")
    return nutrient_id, name, unit, _as_finite_float(raw_amount)


def _nutrient_amount(nutrients: list, nutrient_id: int) -> float | None:
    for entry in nutrients:
        current_id, _, _, amount = _nutrient_fields(entry)
        if current_id == nutrient_id and amount is not None:
            return amount
    return None


def _energy_per_100g(
    nutrients: list,
    data_type: str,
) -> tuple[float | None, int | None, str | None]:
    """Select one USDA energy nutrient deterministically and return kcal.

    Foundation/Experimental foods use the newer Atwater fields, preferring
    food-specific factor 2048 over general factor 2047. Other USDA data types
    continue to use legacy kcal nutrient 1008. A kJ value is converted once.
    Energy nutrients are never added together and list order is irrelevant.
    """
    if data_type in {"Foundation", "Experimental"}:
        priority = (2048, 2047, 1008)
    else:
        priority = (1008, 2048, 2047)

    by_id: dict[int, tuple[float, str]] = {}
    for entry in nutrients:
        nutrient_id, _, unit, amount = _nutrient_fields(entry)
        if nutrient_id is None or amount is None or amount < 0:
            continue
        if nutrient_id in _ENERGY_IDS or nutrient_id == _ENERGY_KJ_ID:
            by_id.setdefault(nutrient_id, (amount, unit.strip().lower()))

    for nutrient_id in priority:
        candidate = by_id.get(nutrient_id)
        if candidate is None:
            continue
        amount, unit = candidate
        if unit in {"kcal", "kilocalorie", "kilocalories"}:
            return amount, nutrient_id, "kcal"
        if unit in {"kj", "kilojoule", "kilojoules"}:
            return amount / 4.184, nutrient_id, "kJ"

    # USDA nutrient 1062 is the explicit kJ energy field. Use it only when no
    # preferred kcal/Atwater nutrient is present.
    fallback = by_id.get(_ENERGY_KJ_ID)
    if fallback is not None:
        amount, unit = fallback
        if unit in {"kj", "kilojoule", "kilojoules"}:
            return amount / 4.184, _ENERGY_KJ_ID, "kJ"

    return None, None, None


def _serving_size_g(item: dict) -> float | None:
    """Return a serving's real gram weight, never an assumed liquid density."""
    size = _as_finite_float(item.get("servingSize"))
    unit = str(item.get("servingSizeUnit") or "").strip().upper()
    if size is None or size <= 0:
        return None
    if unit in _GRAM_SERVING_UNITS:
        return size
    if unit in {"OZ", "OZA", "OUNCE", "OUNCES"}:
        return size * 28.349523125
    # ml cannot be converted to grams without a food-specific density.
    return None


def _label_value(label_nutrients: dict, key: str) -> float | None:
    entry = label_nutrients.get(key)
    if not isinstance(entry, dict):
        return None
    return _as_finite_float(entry.get("value"))


def _has_branded_volume_basis(item: dict) -> bool:
    """Whether a Branded row is normalized to volume rather than grams.

    USDA derives Branded ``foodNutrients`` from label values by scaling them to
    100 of the declared serving-size unit. For an mL serving that means per
    100 mL, not per 100 g. Without a supplied density or gram portion, that row
    cannot satisfy Jugurtha Fit's per-100-g contract.
    """
    if str(item.get("dataType") or "") != "Branded":
        return False
    unit = re.sub(
        r"\s+",
        " ",
        str(item.get("servingSizeUnit") or "").strip().upper(),
    )
    return unit in _VOLUME_SERVING_UNITS


def _has_direct_gram_nutrient_basis(item: dict) -> bool:
    """Whether foodNutrients can be consumed as canonical 100 g values."""
    if str(item.get("dataType") or "") != "Branded":
        return True
    unit = str(item.get("servingSizeUnit") or "").strip().upper()
    return unit in _GRAM_SERVING_UNITS


def _portion_options(item: dict) -> list[dict]:
    """Return only USDA portions that include a trustworthy gram weight."""
    portions: list[dict] = []
    seen: set[tuple[str, float]] = set()

    candidates = list(item.get("foodMeasures") or []) + list(
        item.get("foodPortions") or []
    )
    for portion in candidates:
        gram_weight = _as_finite_float(portion.get("gramWeight"))
        if gram_weight is None or gram_weight <= 0:
            continue
        measure = portion.get("measureUnit") or {}
        description = str(
            portion.get("disseminationText")
            or portion.get("portionDescription")
            or portion.get("modifier")
            or measure.get("name")
            or ""
        ).strip()
        if not description or description.lower() == "quantity not specified":
            continue
        key = (description.casefold(), round(gram_weight, 4))
        if key in seen:
            continue
        seen.add(key)
        portions.append(
            {
                "description": description,
                "gram_weight": round(gram_weight, 2),
                "amount": _as_finite_float(portion.get("amount")),
                "measure_unit": str(
                    portion.get("measureUnitName")
                    or measure.get("name")
                    or ""
                ).strip()
                or None,
            }
        )

    serving_g = _serving_size_g(item)
    if serving_g is not None:
        description = str(item.get("householdServingFullText") or "").strip()
        if not description:
            size = _as_finite_float(item.get("servingSize"))
            unit = str(item.get("servingSizeUnit") or "g").strip()
            description = f"{size:g} {unit}" if size is not None else "Serving"
        key = (description.casefold(), round(serving_g, 4))
        if key not in seen:
            portions.append(
                {
                    "description": description,
                    "gram_weight": round(serving_g, 2),
                    "amount": 1.0,
                    "measure_unit": "serving",
                }
            )

    return portions[:12]


def _sanity_error(
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
) -> str | None:
    values = (calories, protein, carbs, fat)
    if any(not math.isfinite(value) for value in values):
        return "non-finite nutrient"
    if any(value < 0 for value in values):
        return "negative nutrient"
    if calories > _MAX_KCAL_PER_100G:
        return "energy exceeds physical per-100g limit"
    if any(
        value > _MAX_MACRO_G_PER_100G for value in (protein, carbs, fat)
    ):
        return "macro exceeds physical per-100g limit"
    if protein + carbs + fat > _MAX_MACRO_TOTAL_G_PER_100G:
        return "macro total exceeds physical per-100g limit"
    if calories == 0 and protein * 4 + carbs * 4 + fat * 9 > 5:
        return "zero energy conflicts with macronutrients"
    return None


def _parse_food_item(item: dict) -> dict | None:
    """Normalize one USDA result to a single per-100g response contract."""
    nutrients = list(item.get("foodNutrients") or [])
    data_type = str(item.get("dataType") or "")
    fdc_id = item.get("fdcId")

    if _has_branded_volume_basis(item):
        _food_logger.warning(
            "Rejected USDA food fdc_id=%s data_type=%s reason=volume basis has no gram conversion",
            fdc_id,
            data_type,
        )
        return None

    direct_nutrients = nutrients if _has_direct_gram_nutrient_basis(item) else []
    calories, energy_id, raw_energy_unit = _energy_per_100g(
        direct_nutrients,
        data_type,
    )
    protein = _nutrient_amount(direct_nutrients, _MACRO_IDS["protein"])
    carbs = _nutrient_amount(direct_nutrients, _MACRO_IDS["carbs"])
    fat = _nutrient_amount(direct_nutrients, _MACRO_IDS["fat"])
    nutrient_basis = "per_100g"

    # Non-volume foodNutrients use the USDA 100 g basis. Missing is distinct
    # from an explicitly reported zero: require one complete canonical set.
    if any(value is None for value in (calories, protein, carbs, fat)):
        label_nutrients = item.get("labelNutrients") or {}
        serving_g = _serving_size_g(item)
        label_calories = _label_value(label_nutrients, "calories")
        label_protein = _label_value(label_nutrients, "protein")
        label_carbs = _label_value(label_nutrients, "carbohydrates")
        label_fat = _label_value(label_nutrients, "fat")
        label_values = (
            label_calories,
            label_protein,
            label_carbs,
            label_fat,
        )
        if serving_g is not None and all(
            value is not None and value >= 0 for value in label_values
        ):
            factor = 100.0 / serving_g
            calories = label_calories * factor
            protein = label_protein * factor
            carbs = label_carbs * factor
            fat = label_fat * factor
            nutrient_basis = "per_serving_converted"
            raw_energy_unit = "kcal"

    name = str(item.get("description") or "").strip()
    if (
        any(value is None for value in (calories, protein, carbs, fat))
        or not name
        or fdc_id is None
    ):
        _food_logger.warning(
            "Rejected USDA food fdc_id=%s data_type=%s reason=missing required normalized nutrient",
            fdc_id,
            data_type,
        )
        return None

    error = _sanity_error(calories, protein, carbs, fat)
    if error is not None:
        _food_logger.warning(
            "Rejected USDA food fdc_id=%s data_type=%s reason=%s",
            fdc_id,
            data_type,
            error,
        )
        return None

    calories = round(calories, 2)
    protein = round(protein, 2)
    carbs = round(carbs, 2)
    fat = round(fat, 2)
    brand = item.get("brandOwner") or item.get("brandName")

    return {
        "id": f"usda:{fdc_id}",
        "source": "usda",
        "source_id": str(fdc_id),
        "fdc_id": fdc_id,
        "name": name,
        "brand_name": brand,
        "brand": brand,
        "data_type": data_type,
        "calories_per_100g": calories,
        "protein_per_100g": protein,
        "carbs_per_100g": carbs,
        "fat_per_100g": fat,
        # Compatibility aliases for existing mobile clients. These have the
        # exact same per-100g semantics and are never rescaled here.
        "calories": calories,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
        "basis_grams": 100,
        "serving_size_g": 100,
        "nutrient_basis": nutrient_basis,
        "energy_nutrient_id": energy_id,
        "energy_source_unit": raw_energy_unit,
        "portions": _portion_options(item),
    }


def _normalize_search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _search_tokens(value: str) -> list[str]:
    tokens = _normalize_search_text(value).split()
    return [
        token[:-1] if len(token) > 3 and token.endswith("s") else token
        for token in tokens
    ]


def _brand_core(value: str) -> str:
    return " ".join(
        token
        for token in _normalize_search_text(value).split()
        if token not in _CORPORATE_WORDS
    )


def _has_branded_intent(query: str, foods: list[dict]) -> bool:
    normalized_query = _normalize_search_text(query)
    for food in foods:
        core = _brand_core(str(food.get("brand_name") or ""))
        if len(core) >= 3 and core in normalized_query:
            return True
    return False


def _raw_brand_fallback_query(query: str, items: list[dict]) -> str | None:
    """Return a generic product query when matched branded liquids are unsafe."""
    normalized_query = _normalize_search_text(query)
    query_tokens = normalized_query.split()
    matching_brands = []
    for item in items:
        if str(item.get("dataType") or "") != "Branded":
            continue
        core = _brand_core(
            str(item.get("brandOwner") or item.get("brandName") or "")
        )
        if len(core) >= 3 and core in normalized_query:
            matching_brands.append(core)
    if not matching_brands or not query_tokens:
        return None

    brand_tokens = set(max(matching_brands, key=len).split())
    product_tokens = [token for token in query_tokens if token not in brand_tokens]
    # "Chobani Greek yogurt" becomes "greek yogurt". When the brand occupies
    # the entire query (for example "Coca Cola"), the final noun-like token is
    # the safest small generic fallback.
    return " ".join(product_tokens or query_tokens[-1:])


def _search_score(
    food: dict,
    query: str,
    branded_intent: bool,
    original_index: int,
) -> tuple[int, int]:
    normalized_query = _normalize_search_text(query)
    normalized_name = _normalize_search_text(str(food.get("name") or ""))
    query_tokens = _search_tokens(query)
    name_tokens = _search_tokens(str(food.get("name") or ""))

    if normalized_name == normalized_query:
        relevance = 200
    elif name_tokens[: len(query_tokens)] == query_tokens:
        relevance = 150
    elif (
        query_tokens
        and name_tokens
        and name_tokens[0] in query_tokens
        and all(token in name_tokens for token in query_tokens)
    ):
        # USDA commonly writes generic names as "Rice, white" while the user
        # naturally searches "white rice".
        relevance = 145
    elif normalized_query and normalized_query in normalized_name:
        relevance = 125 if len(query_tokens) > 1 else 100
    elif query_tokens and all(token in name_tokens for token in query_tokens):
        relevance = 105
    else:
        overlap = sum(token in name_tokens for token in query_tokens)
        relevance = overlap * 25

    brand = _brand_core(str(food.get("brand_name") or ""))
    brand_match = bool(brand and brand in normalized_query)
    if branded_intent:
        source_score = 40 if food.get("data_type") == "Branded" else 0
        brand_score = 300 if brand_match else 0
    else:
        source_score = _GENERIC_DATA_TYPE_SCORE.get(
            str(food.get("data_type") or ""),
            0,
        )
        brand_score = 0

    if {"nfs", "plain", "raw", "unprepared"}.intersection(name_tokens):
        simple_bonus = 35
    elif _SIMPLE_FOOD_WORDS.intersection(name_tokens):
        simple_bonus = 10
    else:
        simple_bonus = 0
    prepared_penalty = (
        40 if _PREPARED_FOOD_WORDS.intersection(name_tokens) else 0
    )
    unrequested_mixed_drink_penalty = (
        60
        if _UNREQUESTED_MIXED_DRINK_WORDS.intersection(name_tokens)
        and not _UNREQUESTED_MIXED_DRINK_WORDS.intersection(query_tokens)
        else 0
    )
    extra_words = max(0, len(name_tokens) - len(query_tokens))
    score = (
        relevance
        + source_score
        + brand_score
        + simple_bonus
        - prepared_penalty
        - unrequested_mixed_drink_penalty
        - min(30, extra_words * 3)
    )
    return score, -original_index


def _normalize_and_rank_foods(items: list[dict], query: str) -> list[dict]:
    parsed = []
    for index, item in enumerate(items):
        normalized = _parse_food_item(item)
        if normalized is not None:
            parsed.append((index, normalized))

    foods = [food for _, food in parsed]
    branded_intent = _has_branded_intent(query, foods)
    ranking_query = query
    if not branded_intent:
        ranking_query = _raw_brand_fallback_query(query, items) or query
    parsed.sort(
        key=lambda entry: _search_score(
            entry[1],
            ranking_query,
            branded_intent,
            entry[0],
        ),
        reverse=True,
    )

    deduplicated = []
    seen: set[tuple[str, ...]] = set()
    for _, food in parsed:
        name_key = _normalize_search_text(str(food.get("name") or ""))
        if branded_intent:
            key = (name_key, _brand_core(str(food.get("brand_name") or "")))
        else:
            key = (name_key,)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(food)
    return deduplicated


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

    # USDA often puts exact-title Branded rows before the generic Foundation,
    # FNDDS, or SR food. Fetch a bounded candidate pool and rank it locally.
    usda_limit = min(max(limit * 5, 50), 200)
    cache_key = f"normalized-v3:{effective_q.lower()}:{usda_limit}"

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
            cached = _normalize_and_rank_foods(
                list(data.get("foods", [])),
                effective_q,
            )

            # USDA's lexical search can return only Branded volume rows for a
            # natural two-word query such as "whole milk", while its reversed
            # noun-first form ("milk whole") exposes the valid FNDDS entry.
            # Retry only when no safe canonical result survived normalization.
            query_tokens = _normalize_search_text(effective_q).split()
            if not cached and len(query_tokens) == 2:
                reversed_query = " ".join(reversed(query_tokens))
                fallback_response = httpx.get(
                    f"{USDA_BASE_URL}/foods/search",
                    params={
                        "query": reversed_query,
                        "pageSize": usda_limit,
                        "api_key": USDA_API_KEY,
                    },
                    timeout=10.0,
                )
                if fallback_response.status_code == 200:
                    fallback_data = fallback_response.json()
                    cached = _normalize_and_rank_foods(
                        list(fallback_data.get("foods", [])),
                        effective_q,
                    )

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
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="USDA returned unusable normalized nutrition for this food",
            )

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
