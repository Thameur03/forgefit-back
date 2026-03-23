from fastapi.testclient import TestClient
from main import app
from auth.utils import create_access_token

client = TestClient(app)
# patch get_current_user to return a fake user
from models.user import User
from routers import workouts

async def override_get_current_user():
    return User(id=1, email="test@test.com", weight_kg=80.0)

app.dependency_overrides[workouts.get_current_user] = override_get_current_user

# create a mock DB session
class MockQuery:
    def options(self, *args): return self
    def filter(self, *args): return self
    def order_by(self, *args): return self
    def offset(self, *args): return self
    def limit(self, *args): return self
    def all(self):
        from models.workout import Workout
        return [Workout(id=1, user_id=1, date="2026-03-18", duration_seconds=3600, name="Test")]

class MockDB:
    def query(self, *args): return MockQuery()

from database import get_db
app.dependency_overrides[get_db] = lambda: MockDB()

resp = client.get("/workouts/")
print("RESPONSE:", resp.json())
