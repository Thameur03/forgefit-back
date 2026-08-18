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
