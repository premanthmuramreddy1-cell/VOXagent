import os
import tempfile

os.environ.update(
    VOX_SECRET_KEY="test-secret-key-at-least-32-bytes-long!!",
    VOX_DATABASE_URL="sqlite:///:memory:",
    VOX_DATA_DIR=tempfile.mkdtemp(),
    VOX_ADMIN_EMAILS='["admin@example.com"]',
    VOX_AUTH_RATE_PER_MIN="1000",
)

import pytest
from fastapi.testclient import TestClient

from app.main import app

_n = 0


@pytest.fixture
def client():
    return TestClient(app)


def signup(client, email=None):
    global _n
    _n += 1
    email = email or f"user{_n}@example.com"
    r = client.post("/auth/signup", json={"email": email, "password": "password123", "name": "Asha"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


@pytest.fixture
def auth(client):
    return signup(client)[0]


@pytest.fixture
def token(client):
    return signup(client)[1]["access_token"]
