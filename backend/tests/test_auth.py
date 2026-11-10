from app.config import settings
from app.security import _hits
from tests.conftest import signup


def test_signup_login_and_me(client):
    headers, body = signup(client, "asha@example.com")
    assert body["user"]["email"] == "asha@example.com"
    assert client.get("/me", headers=headers).json()["name"] == "Asha"
    ok = client.post("/auth/login", json={"email": "ASHA@example.com", "password": "password123"})
    assert ok.status_code == 200
    bad = client.post("/auth/login", json={"email": "asha@example.com", "password": "wrong-password"})
    assert bad.status_code == 401


def test_duplicate_email_and_weak_password_rejected(client):
    signup(client, "dup@example.com")
    again = client.post("/auth/signup", json={"email": "dup@example.com", "password": "password123"})
    assert again.status_code == 409
    weak = client.post("/auth/signup", json={"email": "weak@example.com", "password": "short"})
    assert weak.status_code == 422


def test_me_requires_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_refresh_token_rotates_and_logout_revokes(client):
    signup(client)
    old = client.cookies["vox_refresh"]
    assert client.post("/auth/refresh").status_code == 200
    assert client.cookies["vox_refresh"] != old
    client.cookies.clear()
    client.cookies.set("vox_refresh", old)
    assert client.post("/auth/refresh").status_code == 401  # a used token is dead
    signup(client)
    client.post("/auth/logout")
    assert client.post("/auth/refresh").status_code == 401


def test_admin_flag_comes_from_config(client):
    assert signup(client, "admin@example.com")[1]["user"]["is_admin"] is True
    assert signup(client)[1]["user"]["is_admin"] is False


def test_export_then_delete_removes_account(client):
    headers, _ = signup(client, "gone@example.com")
    client.patch("/me", headers=headers, json={"preferred_language": "te"})
    export = client.get("/me/export", headers=headers).json()
    assert export["user"]["preferred_language"] == "te"
    assert "password_hash" not in str(export)
    assert client.delete("/me", headers=headers).json() == {"ok": True}
    assert client.get("/me", headers=headers).status_code == 401


def test_auth_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(settings(), "auth_rate_per_min", 2)
    _hits.clear()
    codes = [
        client.post("/auth/login", json={"email": "x@example.com", "password": "password123"}).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 429]
    _hits.clear()
