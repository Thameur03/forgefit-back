from fastapi.testclient import TestClient
from main import app
from database import Base, engine, SessionLocal
from models.user import User

client = TestClient(app)

def test_flow():
    # Attempt to sign up or get a token
    # Let's create a distinct user for the test
    import random
    email = f"test_{random.randint(1000, 9999)}@example.com"
    password = "Password123"
    
    # Create user directly using SQLAlchemy to avoid email sending hang
    db = SessionLocal()
    from auth.utils import hash_password
    new_user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name="Test User",
        is_verified=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    db.close()
    
    print("User created directly in DB")
    
    # Login
    res = client.post("/auth/login", json={
        "email": email,
        "password": password
    })
    print("Login Response Data:", res.text)
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Post workout
    workout_data = {
        "name": "Push Day",
        "duration_seconds": 3600,
        "notes": "Felt good"
    }
    res = client.post("/workouts/", json=workout_data, headers=headers)
    print("POST /workouts/ status:", res.status_code)
    try:
        workout = res.json()
        print("POST Response Data:", workout)
        workout_id = workout["id"]
        assert workout["name"] == "Push Day", f"Expected Push Day, got {workout.get('name')}"
        assert workout["duration_seconds"] == 3600, f"Expected 3600, got {workout.get('duration_seconds')}"
    except Exception as e:
        print("POST failed:", e)
        return

    # 2. Get workouts list
    res = client.get("/workouts/", headers=headers)
    print("\nGET /workouts/ status:", res.status_code)
    try:
        workouts_list = res.json()
        # print("GET List Response:", workouts_list)
        w = workouts_list[0]
        assert w["name"] == "Push Day"
        assert w["duration_seconds"] == 3600
        print("GET List assertions passed.")
    except Exception as e:
        print("GET list failed:", e)

    # 3. Add a set to the workout
    set_data = {
        "exercise_name": "Bench Press",
        "sets": 3,
        "reps": 10,
        "weight_kg": 60.0
    }
    res = client.post(f"/workouts/{workout_id}/sets", json=set_data, headers=headers)
    print(f"\nPOST /workouts/{workout_id}/sets status:", res.status_code)
    try:
        w_set = res.json()
        print("Set created:", w_set)
        set_id = w_set["id"]
    except Exception as e:
        print("POST set failed:", e)
        return
        
    # 4. Delete the set
    res = client.delete(f"/workouts/{workout_id}/sets/{set_id}", headers=headers)
    print(f"\nDELETE /workouts/{workout_id}/sets/{set_id} status:", res.status_code)
    print("DELETE response:", res.json())

if __name__ == "__main__":
    test_flow()
