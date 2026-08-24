"""Deterministic USDA normalization and search-ranking regression tests."""

import math

import pytest

from routers import food_search


def _nutrient(
    nutrient_id: int,
    value: float,
    unit: str,
    name: str,
) -> dict:
    return {
        "nutrientId": nutrient_id,
        "nutrientName": name,
        "unitName": unit,
        "value": value,
    }


def _food(
    fdc_id: int,
    name: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    *,
    data_type: str = "Survey (FNDDS)",
    **extra,
) -> dict:
    return {
        "fdcId": fdc_id,
        "description": name,
        "dataType": data_type,
        "foodNutrients": [
            _nutrient(1008, calories, "KCAL", "Energy"),
            _nutrient(1003, protein, "G", "Protein"),
            _nutrient(1005, carbs, "G", "Carbohydrate, by difference"),
            _nutrient(1004, fat, "G", "Total lipid (fat)"),
        ],
        **extra,
    }


def test_gram_basis_food_nutrients_are_not_rescaled_by_label_serving():
    item = _food(
        2012128,
        "BANANA",
        312,
        3,
        40,
        15,
        data_type="Branded",
        servingSize=32,
        servingSizeUnit="g",
    )

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 312
    assert parsed["nutrient_basis"] == "per_100g"
    assert parsed["basis_grams"] == 100


@pytest.mark.parametrize(
    ("fdc_id", "name", "serving_ml", "calories", "protein", "carbs", "fat"),
    [
        (2620729, "WHOLE MILK", 240, 62, 3.33, 5, 3.33),
        (2742541, "Coca-Cola Bottle, 2 Liters", 355, 39, 0, 10.99, 0),
        (1905976, "OLIVE OIL", 15, 800, 0, 0, 93.33),
    ],
)
def test_branded_per_100ml_food_is_not_mislabeled_as_per_100g(
    fdc_id,
    name,
    serving_ml,
    calories,
    protein,
    carbs,
    fat,
):
    item = _food(
        fdc_id,
        name,
        calories,
        protein,
        carbs,
        fat,
        data_type="Branded",
        servingSize=serving_ml,
        servingSizeUnit="MLT",
    )

    assert food_search._parse_food_item(item) is None


def test_branded_food_without_a_declared_mass_basis_is_rejected():
    item = _food(
        504,
        "Unknown-basis branded food",
        200,
        5,
        20,
        10,
        data_type="Branded",
    )

    assert food_search._parse_food_item(item) is None


def test_explicit_zero_protein_is_a_real_value_and_is_accepted():
    parsed = food_search._parse_food_item(
        _food(2710186, "Olive oil", 900, 0, 0, 100)
    )

    assert parsed is not None
    assert parsed["protein_per_100g"] == 0


@pytest.mark.parametrize("missing_id", [1003, 1005, 1004])
def test_missing_required_macro_is_rejected_instead_of_becoming_zero(missing_id):
    item = _food(600, "Incomplete food", 100, 2, 20, 1)
    item["foodNutrients"] = [
        nutrient
        for nutrient in item["foodNutrients"]
        if nutrient["nutrientId"] != missing_id
    ]

    assert food_search._parse_food_item(item) is None


def test_legacy_energy_uses_1008_kcal_instead_of_first_energy_name():
    item = _food(173944, "Bananas, raw", 89, 1.09, 22.8, 0.33)
    item["dataType"] = "SR Legacy"
    item["foodNutrients"].insert(
        0,
        _nutrient(1062, 371, "kJ", "Energy"),
    )

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 89
    assert parsed["energy_nutrient_id"] == 1008
    assert parsed["energy_source_unit"] == "kcal"


def test_foundation_energy_prefers_specific_2048_without_adding_candidates():
    item = _food(
        1750340,
        "Apples, fuji, with skin, raw",
        60,
        0.15,
        15.7,
        0.16,
        data_type="Foundation",
    )
    item["foodNutrients"][:1] = [
        _nutrient(1008, 60, "KCAL", "Energy"),
        _nutrient(2047, 64.7, "KCAL", "Energy (Atwater General Factors)"),
        _nutrient(2048, 58.2, "KCAL", "Energy (Atwater Specific Factors)"),
    ]

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 58.2
    assert parsed["energy_nutrient_id"] == 2048


@pytest.mark.parametrize("nutrient_id", [1008, 2047, 2048])
def test_kilojoules_are_converted_to_kcal_exactly_once(nutrient_id):
    item = _food(10 + nutrient_id, "Energy fixture", 0, 1, 10, 1)
    item["dataType"] = "Foundation" if nutrient_id != 1008 else "SR Legacy"
    item["foodNutrients"][0] = _nutrient(
        nutrient_id,
        418.4,
        "kJ",
        "Energy",
    )

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 100
    assert parsed["energy_nutrient_id"] == nutrient_id
    assert parsed["energy_source_unit"] == "kJ"


def test_explicit_1062_kj_is_only_used_as_a_final_fallback():
    item = _food(1062, "kJ fallback", 0, 1, 10, 1)
    item["foodNutrients"][0] = _nutrient(1062, 418.4, "kJ", "Energy")

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 100
    assert parsed["energy_nutrient_id"] == 1062


def test_nested_detail_nutrients_use_the_same_per_100g_contract():
    compact = _food(2709224, "Banana, raw", 97, 0.74, 22.71, 0.28)
    compact["foodNutrients"] = [
        {
            "nutrient": {
                "id": nutrient["nutrientId"],
                "name": nutrient["nutrientName"],
                "unitName": nutrient["unitName"],
            },
            "amount": nutrient["value"],
        }
        for nutrient in compact["foodNutrients"]
    ]

    parsed = food_search._parse_food_item(compact)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 97
    assert parsed["protein_per_100g"] == 0.74


def test_label_nutrients_convert_from_a_real_gram_serving_once():
    item = {
        "fdcId": 500,
        "description": "Label-only soup",
        "dataType": "Branded",
        "servingSize": 200,
        "servingSizeUnit": "g",
        "foodNutrients": [],
        "labelNutrients": {
            "calories": {"value": 165},
            "protein": {"value": 10},
            "carbohydrates": {"value": 20},
            "fat": {"value": 5},
        },
    }

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["calories_per_100g"] == 82.5
    assert parsed["protein_per_100g"] == 5
    assert parsed["carbs_per_100g"] == 10
    assert parsed["fat_per_100g"] == 2.5
    assert parsed["nutrient_basis"] == "per_serving_converted"


def test_label_nutrients_preserve_explicit_zero_macros():
    item = {
        "fdcId": 502,
        "description": "Label-only oil",
        "dataType": "Branded",
        "servingSize": 15,
        "servingSizeUnit": "g",
        "foodNutrients": [],
        "labelNutrients": {
            "calories": {"value": 135},
            "protein": {"value": 0},
            "carbohydrates": {"value": 0},
            "fat": {"value": 15},
        },
    }

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["protein_per_100g"] == 0
    assert parsed["carbs_per_100g"] == 0


@pytest.mark.parametrize("missing_key", ["protein", "carbohydrates", "fat"])
def test_incomplete_label_nutrients_are_rejected(missing_key):
    label_nutrients = {
        "calories": {"value": 100},
        "protein": {"value": 2},
        "carbohydrates": {"value": 20},
        "fat": {"value": 1},
    }
    del label_nutrients[missing_key]
    item = {
        "fdcId": 503,
        "description": "Incomplete label food",
        "dataType": "Branded",
        "servingSize": 100,
        "servingSizeUnit": "g",
        "foodNutrients": [],
        "labelNutrients": label_nutrients,
    }

    assert food_search._parse_food_item(item) is None


def test_label_only_liquid_without_density_is_not_given_an_invented_weight():
    item = {
        "fdcId": 501,
        "description": "Label-only drink",
        "dataType": "Branded",
        "servingSize": 240,
        "servingSizeUnit": "ml",
        "foodNutrients": [],
        "labelNutrients": {"calories": {"value": 120}},
    }

    assert food_search._parse_food_item(item) is None


@pytest.mark.parametrize(
    ("calories", "protein", "carbs", "fat"),
    [
        (1001, 0, 0, 100),
        (-1, 0, 0, 0),
        (100, 101, 0, 0),
        (100, 40, 40, 30),
        (math.inf, 0, 0, 0),
        (math.nan, 0, 0, 0),
    ],
)
def test_impossible_normalized_results_are_rejected(
    calories,
    protein,
    carbs,
    fat,
):
    assert (
        food_search._parse_food_item(
            _food(9000, "Invalid fixture", calories, protein, carbs, fat)
        )
        is None
    )


def test_generic_ranking_prefers_simple_foods_and_deduplicates_titles():
    items = [
        _food(
            1,
            "BANANA",
            312,
            3,
            40,
            15,
            data_type="Branded",
            brandOwner="Example Foods Inc.",
        ),
        _food(2, "Banana chips", 519, 2, 58, 34),
        _food(3, "Banana, raw", 97, 0.74, 22.7, 0.28),
        _food(4, "Banana, raw", 96, 0.7, 22, 0.3),
    ]

    ranked = food_search._normalize_and_rank_foods(items, "banana")

    assert ranked[0]["fdc_id"] == 3
    assert [food["name"] for food in ranked].count("Banana, raw") == 1


def test_generic_olive_oil_beats_branded_exact_title():
    items = [
        _food(
            1905976,
            "OLIVE OIL",
            800,
            0,
            0,
            93.3,
            data_type="Branded",
            brandOwner="Example Foods Inc.",
        ),
        _food(2710186, "Olive oil", 900, 0, 0, 100),
    ]

    ranked = food_search._normalize_and_rank_foods(items, "olive oil")

    assert ranked[0]["fdc_id"] == 2710186


def test_broad_rice_query_prefers_unspecified_rice_over_composite_dish():
    items = [
        _food(2709089, "Rice dressing", 110, 1.98, 17.61, 3.29),
        _food(2708402, "Rice, cooked, NFS", 129, 2.66, 27.9, 0.28),
    ]

    ranked = food_search._normalize_and_rank_foods(items, "rice")

    assert ranked[0]["fdc_id"] == 2708402


def test_brand_query_keeps_branded_foods_relevant():
    items = [
        _food(
            10,
            "Coca-Cola Bottle, 2 Liters",
            39,
            0,
            11,
            0,
            data_type="Branded",
            brandOwner="The Coca-Cola Company",
            servingSize=100,
            servingSizeUnit="g",
        ),
        _food(
            11,
            "Beverages, cola, regular",
            41,
            0,
            10.6,
            0,
            data_type="SR Legacy",
        ),
    ]

    ranked = food_search._normalize_and_rank_foods(items, "Coca Cola")

    assert ranked[0]["fdc_id"] == 10
    assert ranked[0]["data_type"] == "Branded"


def test_unsafe_branded_liquid_query_falls_back_to_generic_product():
    items = [
        _food(
            2742541,
            "Coca-Cola Bottle, 2 Liters",
            39,
            0,
            10.99,
            0,
            data_type="Branded",
            brandOwner="The Coca-Cola Company",
            servingSize=355,
            servingSizeUnit="MLT",
        ),
        _food(
            171884,
            "Beverages, The COCA-COLA company, Minute Maid, Lemonade",
            46,
            0,
            12.1,
            0,
            data_type="SR Legacy",
        ),
        _food(2710657, "Whiskey and cola", 89, 0, 10, 0),
        _food(2710541, "Soft drink, cola", 42, 0, 10.6, 0),
    ]

    ranked = food_search._normalize_and_rank_foods(items, "Coca Cola")

    assert ranked[0]["fdc_id"] == 2710541
    assert ranked[0]["name"] == "Soft drink, cola"


def test_reliable_usda_portions_are_preserved_and_unknown_weights_are_not():
    item = _food(
        2709224,
        "Banana, raw",
        97,
        0.74,
        22.71,
        0.28,
        foodMeasures=[
            {
                "disseminationText": "1 banana",
                "gramWeight": 126,
            },
            {
                "disseminationText": "Quantity not specified",
                "gramWeight": 126,
            },
            {
                "disseminationText": "1 mystery unit",
                "gramWeight": None,
            },
        ],
    )

    parsed = food_search._parse_food_item(item)

    assert parsed is not None
    assert parsed["portions"] == [
        {
            "description": "1 banana",
            "gram_weight": 126.0,
            "amount": None,
            "measure_unit": None,
        }
    ]


@pytest.mark.parametrize(
    ("name", "calories", "protein", "carbs", "fat", "ranges"),
    [
        ("Banana, raw", 97, 0.74, 22.71, 0.28, ((70, 130), (0, 3), (15, 35), (0, 2))),
        ("Olive oil", 900, 0, 0, 100, ((800, 1000), (0, 1), (0, 1), (90, 101))),
        ("Apple, raw", 61, 0.17, 14.8, 0.15, ((35, 90), (0, 2), (8, 25), (0, 2))),
        ("Chicken breast, raw", 112, 22.5, 0, 1.93, ((90, 220), (18, 40), (0, 3), (0, 12))),
        ("White rice, cooked", 130, 2.7, 28, 0.3, ((80, 180), (1, 6), (18, 40), (0, 4))),
        ("Egg, whole", 143, 12.4, 0.96, 9.96, ((120, 190), (9, 16), (0, 4), (7, 14))),
        ("Whole milk", 62, 3.3, 5, 3.3, ((45, 85), (2, 5), (3, 7), (2, 5))),
        ("Peanut butter", 598, 22.2, 22.3, 51.4, ((450, 700), (15, 35), (10, 35), (35, 65))),
        ("Oats, raw", 379, 13.2, 67.7, 6.5, ((300, 450), (8, 20), (50, 80), (3, 12))),
        ("Salmon, raw", 188, 20.4, 0, 11.2, ((110, 260), (16, 28), (0, 2), (3, 18))),
        ("Broccoli, raw", 39, 2.6, 6.3, 0.3, ((20, 60), (1, 5), (3, 12), (0, 2))),
    ],
)
def test_golden_generic_foods_stay_in_broad_physical_ranges(
    name,
    calories,
    protein,
    carbs,
    fat,
    ranges,
):
    parsed = food_search._parse_food_item(
        _food(7000, name, calories, protein, carbs, fat)
    )

    assert parsed is not None
    for value, (lower, upper) in zip(
        (
            parsed["calories_per_100g"],
            parsed["protein_per_100g"],
            parsed["carbs_per_100g"],
            parsed["fat_per_100g"],
        ),
        ranges,
    ):
        assert lower <= value <= upper
