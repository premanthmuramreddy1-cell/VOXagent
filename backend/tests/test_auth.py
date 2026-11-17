import pytest
import asyncio
from unittest.mock import AsyncMock

import httpx

from app.api import auth as auth_api
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


def test_admin_email_cannot_be_claimed_by_a_remote_password_sign_up(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings(), "admin_emails", ["owner@example.com"])
    stranger = TestClient(app, client=("203.0.113.9", 40000))  # someone reaching a public link
    r = stranger.post("/auth/signup", json={"email": "Owner@example.com", "password": "password123", "name": "X"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is False


def test_locked_sign_up_refuses_strangers_and_tunnelled_requests(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings(), "allowed_emails", ["locked@example.com"])
    _hits.clear()  # earlier tests used these addresses; the sign-up rate limit counts per address
    body = {"email": "locked@example.com", "password": "password123", "name": "X"}
    stranger = TestClient(app, client=("203.0.113.9", 40000))
    assert stranger.post("/auth/signup", json=body).status_code == 403  # an unverified email, even a listed one
    tunnel = TestClient(app, client=("127.0.0.1", 40000))  # cloudflared connects from this Mac too
    assert tunnel.post("/auth/signup", json=body, headers={"cf-connecting-ip": "203.0.113.9"}).status_code == 403
    assert tunnel.post("/auth/signup", json=body).status_code == 200  # the owner, on the Mac itself


def test_locked_google_sign_in_admits_only_listed_emails(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_client_id", "cid")
    monkeypatch.setattr(settings(), "allowed_emails", ["g@example.com"])
    monkeypatch.setattr(auth_api, "_google_claims", AsyncMock(return_value=_claims(email="x@example.com")))
    assert client.post("/auth/google", json={"credential": "tok"}).status_code == 403
    monkeypatch.setattr(auth_api, "_google_claims", AsyncMock(return_value=_claims()))
    assert client.post("/auth/google", json={"credential": "tok"}).status_code == 200


def test_export_then_delete_removes_account(client, tmp_path, monkeypatch):
    from app.skills import doc_writer

    monkeypatch.setattr(doc_writer, "DOCS_ROOT", tmp_path)
    headers, body = signup(client, "gone@example.com")
    (tmp_path / "people" / body["user"]["id"]).mkdir(parents=True)
    (tmp_path / "people" / body["user"]["id"] / "plan.md").write_text("mine")
    client.patch("/me", headers=headers, json={"preferred_language": "te"})
    export = client.get("/me/export", headers=headers).json()
    assert export["user"]["preferred_language"] == "te"
    assert "password_hash" not in str(export)
    assert client.delete("/me", headers=headers).json() == {"ok": True}
    assert client.get("/me", headers=headers).status_code == 401
    assert not (tmp_path / "people" / body["user"]["id"]).exists()  # their documents go with them


def test_auth_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(settings(), "auth_rate_per_min", 2)
    _hits.clear()
    codes = [
        client.post("/auth/login", json={"email": "x@example.com", "password": "password123"}).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 429]
    _hits.clear()


def _claims(**over):
    return {"aud": "cid", "iss": "https://accounts.google.com", "email": "g@example.com",
            "email_verified": "true", "name": "Gita", **over}


def test_google_login_creates_then_reuses_the_account(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_client_id", "cid")
    monkeypatch.setattr(auth_api, "_google_claims", AsyncMock(return_value=_claims()))
    first = client.post("/auth/google", json={"credential": "tok"})
    assert first.status_code == 200 and first.json()["user"]["name"] == "Gita"
    again = client.post("/auth/google", json={"credential": "tok"})
    assert again.json()["user"]["id"] == first.json()["user"]["id"]
    # a Google-only account has no password, so password login must fail
    assert client.post("/auth/login", json={"email": "g@example.com", "password": "password123"}).status_code == 401


def test_google_login_rejects_bad_tokens(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_client_id", "cid")
    for bad in (_claims(aud="someone-else"), _claims(email_verified="false"), _claims(iss="evil.com")):
        monkeypatch.setattr(auth_api, "_google_claims", AsyncMock(return_value=bad))
        assert client.post("/auth/google", json={"credential": "tok"}).status_code == 401


def test_google_login_is_off_without_a_client_id(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_client_id", "")
    assert client.post("/auth/google", json={"credential": "tok"}).status_code == 404
    assert client.get("/auth/config").json() == {"google_client_id": ""}


def test_google_claims_rejects_a_token_google_refuses():
    transport = httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "invalid_token"}))
    assert asyncio.run(auth_api._google_claims("tok", transport)) is None
    ok = httpx.MockTransport(lambda req: httpx.Response(200, json=_claims()))
    assert asyncio.run(auth_api._google_claims("tok", ok))["email"] == "g@example.com"


def _request(host, headers=()):
    from starlette.requests import Request

    return Request({"type": "http", "client": (host, 1), "path": "/auth/login",
                    "headers": [(k.encode(), v.encode()) for k, v in headers]})


def test_rate_limit_counts_each_tunnel_caller_separately(monkeypatch):
    from fastapi import HTTPException

    from app.security import client_ip, rate_limit

    monkeypatch.setattr(settings(), "auth_rate_per_min", 1)
    _hits.clear()
    # behind the tunnel everyone arrives from 127.0.0.1: one caller must not use up everyone's attempts
    rate_limit(_request("127.0.0.1", [("cf-connecting-ip", "203.0.113.5")]))
    rate_limit(_request("127.0.0.1", [("cf-connecting-ip", "198.51.100.7")]))
    with pytest.raises(HTTPException):
        rate_limit(_request("127.0.0.1", [("cf-connecting-ip", "203.0.113.5")]))
    # a direct caller cannot dodge its own limit by claiming another address
    assert client_ip(_request("198.51.100.9", [("x-forwarded-for", "1.2.3.4")])) == "198.51.100.9"
    _hits.clear()


def test_rate_limit_forgets_idle_callers(monkeypatch):
    from app import security

    _hits.clear()
    for i in range(10_001):
        _hits[f"10.0.{i}:/auth/login"].append(-1000.0)
    security.rate_limit(_request("127.0.0.1"))
    assert len(_hits) == 1
    _hits.clear()
