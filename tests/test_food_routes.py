"""Routing and compatibility tests for local and USDA food endpoints."""

from auth.utils import create_access_token, hash_password
from models.food import Food
from models.user import User
from routers import food_search
from tests.support import TestingSessionLocal, client


def _auth_headers() -> dict[str, str]:
    db = TestingSessionLocal()
    user = User(
        email="food-routes@example.com",
        hashed_password=hash_password("Password1"),
        full_name="Food Tester",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.email})
    db.close()
    return {"Authorization": f"Bearer {token}"}


def test_local_path_hits_local_database_handler():
    headers = _auth_headers()
    db = TestingSessionLocal()
    db.add(
        Food(
            name="Local Oats",
            brand="Jugurtha Kitchen",
            calories=380,
            protein_g=13,
            carbs_g=68,
            fat_g=7,
            serving_size_g=100,
            is_active=True,
        )
    )
    db.commit()
    db.close()

    response = client.get("/food/local?search=Oats", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()[0]["name"] == "Local Oats"
    assert response.json()[0]["source"] == "local"


def test_numeric_fdc_id_hits_detail_handler(monkeypatch):
    headers = _auth_headers()
    monkeypatch.setattr(food_search, "_check_api_key", lambda: None)
    food_search._detail_cache[12345] = {
        "fdc_id": 12345,
        "name": "USDA Test Food",
        "brand": None,
        "calories": 100,
        "protein_g": 1,
        "carbs_g": 2,
        "fat_g": 3,
        "serving_size_g": 100,
    }

    response = client.get("/food/12345", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["fdc_id"] == 12345
    assert response.json()["name"] == "USDA Test Food"


def test_non_numeric_dynamic_food_id_returns_validation_error():
    headers = _auth_headers()
    response = client.get("/food/not-a-number", headers=headers)
    assert response.status_code == 422


def test_search_route_normalizes_and_ranks_a_bounded_candidate_pool(monkeypatch):
    headers = _auth_headers()
    captured_params = {}

    class _FakeResponse:
        status_code = 200
        request = None

        @staticmethod
        def json():
            def nutrients(calories, protein, carbs, fat):
                return [
                    {
                        "nutrientId": 1008,
                        "nutrientName": "Energy",
                        "unitName": "KCAL",
                        "value": calories,
                    },
                    {
                        "nutrientId": 1003,
                        "nutrientName": "Protein",
                        "unitName": "G",
                        "value": protein,
                    },
                    {
                        "nutrientId": 1005,
                        "nutrientName": "Carbohydrate, by difference",
                        "unitName": "G",
                        "value": carbs,
                    },
                    {
                        "nutrientId": 1004,
                        "nutrientName": "Total lipid (fat)",
                        "unitName": "G",
                        "value": fat,
                    },
                ]

            return {
                "foods": [
                    {
                        "fdcId": 2012128,
                        "description": "BANANA",
                        "dataType": "Branded",
                        "brandOwner": "Example Foods Inc.",
                        "servingSize": 32,
                        "servingSizeUnit": "g",
                        "foodNutrients": nutrients(312, 3, 40, 15),
                    },
                    {
                        "fdcId": 2709224,
                        "description": "Banana, raw",
                        "dataType": "Survey (FNDDS)",
                        "foodNutrients": nutrients(97, 0.74, 22.71, 0.28),
                    },
                ]
            }

    def fake_get(url, *, params, timeout):
        captured_params.update(params)
        return _FakeResponse()

    food_search._search_cache.clear()
    food_search._stale_search_cache.clear()
    monkeypatch.setattr(food_search, "_check_api_key", lambda: None)
    monkeypatch.setattr(food_search.httpx, "get", fake_get)

    response = client.get("/food/search?q=banana&limit=2", headers=headers)

    assert response.status_code == 200, response.text
    assert captured_params["pageSize"] == 50
    assert response.json()[0]["fdc_id"] == 2709224
    assert response.json()[0]["calories_per_100g"] == 97
    assert response.json()[0]["source"] == "usda"


def test_search_route_retries_reversed_two_word_query_when_volume_rows_fail(
    monkeypatch,
):
    headers = _auth_headers()
    captured_queries = []

    class _FakeResponse:
        status_code = 200
        request = None

        def __init__(self, foods):
            self._foods = foods

        def json(self):
            return {"foods": self._foods}

    def nutrients(calories, protein, carbs, fat):
        return [
            {
                "nutrientId": nutrient_id,
                "nutrientName": name,
                "unitName": unit,
                "value": value,
            }
            for nutrient_id, name, unit, value in [
                (1008, "Energy", "KCAL", calories),
                (1003, "Protein", "G", protein),
                (1005, "Carbohydrate, by difference", "G", carbs),
                (1004, "Total lipid (fat)", "G", fat),
            ]
        ]

    def fake_get(url, *, params, timeout):
        captured_queries.append(params["query"])
        if params["query"] == "whole milk":
            return _FakeResponse(
                [
                    {
                        "fdcId": 2620729,
                        "description": "WHOLE MILK",
                        "dataType": "Branded",
                        "servingSize": 240,
                        "servingSizeUnit": "MLT",
                        "foodNutrients": nutrients(62, 3.33, 5, 3.33),
                    }
                ]
            )
        return _FakeResponse(
            [
                {
                    "fdcId": 2705385,
                    "description": "Milk, whole",
                    "dataType": "Survey (FNDDS)",
                    "foodNutrients": nutrients(61, 3.15, 4.8, 3.27),
                }
            ]
        )

    food_search._search_cache.clear()
    food_search._stale_search_cache.clear()
    monkeypatch.setattr(food_search, "_check_api_key", lambda: None)
    monkeypatch.setattr(food_search.httpx, "get", fake_get)

    response = client.get("/food/search?q=whole%20milk", headers=headers)

    assert response.status_code == 200, response.text
    assert captured_queries == ["whole milk", "milk whole"]
    assert response.json()[0]["fdc_id"] == 2705385
    assert response.json()[0]["calories_per_100g"] == 61
