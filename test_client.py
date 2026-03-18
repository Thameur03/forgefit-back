import requests

# Create User
from database import SessionLocal
from models.user import User
from auth.utils import hash_password
import random

db = SessionLocal()
email = f"client_{random.randint(1000, 9999)}@example.com"
password = "Password123"

new_user = User(
    email=email,
    hashed_password=hash_password(password),
    full_name="Client User",
    is_verified=True
)
db.add(new_user)
db.commit()
print(f"Created user {email}")

BASE_URL = "http://localhost:8081"

# Login
res = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
print("Login status:", res.status_code)
if res.status_code != 200:
    print("Login err:", res.text)
    exit(1)

token = res.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# POST /workouts/
w_data = {"name": "Push Day", "duration_seconds": 3600}
res = requests.post(f"{BASE_URL}/workouts/", json=w_data, headers=headers)
print("POST workout status:", res.status_code)
w = res.json()
print("Workout response:", w)
assert w["name"] == "Push Day"
assert w["duration_seconds"] == 3600
w_id = w["id"]

# GET /workouts/
res = requests.get(f"{BASE_URL}/workouts/", headers=headers)
print("GET workouts status:", res.status_code)
ws = res.json()
print("First workout:", ws[0])
assert ws[0]["name"] == "Push Day"

# POST a set
s_data = {"exercise_name": "Bench Press", "sets": 3, "reps": 10, "weight_kg": 60.0}
res = requests.post(f"{BASE_URL}/workouts/{w_id}/sets", json=s_data, headers=headers)
s_id = res.json()["id"]
print(f"Created set {s_id}")

# GET /workouts/{id}
res = requests.get(f"{BASE_URL}/workouts/{w_id}", headers=headers)
print("GET specific workout status:", res.status_code)
ws_specific = res.json()
assert ws_specific["name"] == "Push Day"

# DELETE a set
res = requests.delete(f"{BASE_URL}/workouts/{w_id}/sets/{s_id}", headers=headers)
print("DELETE set status:", res.status_code, res.text)

print("ALL TESTS PASSED")
