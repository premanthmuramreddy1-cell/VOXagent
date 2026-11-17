import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import billing, bots, github, google, personal, push
from app.config import settings
from app.crypto import decrypt, encrypt, load_secret, save_secret
from app.db import Reminder, SessionLocal, User, new_id
from app.skills import RunContext
from app.skills import personal_tools as pt
from tests.conftest import signup


def user_id() -> str:
    with SessionLocal() as db:
        user = User(email=f"p{new_id()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        return user.id


def ctx_for(uid: str) -> RunContext:
    return RunContext(uid, "s", "", [], AsyncMock(), AsyncMock(return_value=""))


def run(tool, uid, **args):
    return asyncio.run(tool.run(tool.input_schema(**args), ctx_for(uid)))


# ---------------------------------------------------------------- crypto + profile

def test_secrets_are_encrypted_at_rest():
    blob = encrypt({"token": "ghp_abc"})
    assert "ghp_abc" not in blob and decrypt(blob) == {"token": "ghp_abc"}
    uid = user_id()
    with SessionLocal() as db:
        save_secret(db, uid, "github", {"token": "t1"})
        save_secret(db, uid, "github", {"token": "t2"})
        assert load_secret(db, uid, "github") == {"token": "t2"}


def test_profile_never_stores_passwords_or_cards(client):
    headers, _ = signup(client)
    ok = client.put("/me/profile", headers=headers, json={"full_name": "Asha Rao", "city": "Hyderabad"})
    assert ok.json() == {"full_name": "Asha Rao", "city": "Hyderabad"}
    for bad in ({"password": "hunter2"}, {"notes": "4111 1111 1111 1111"}, {"card_cvv": "123"}):
        assert client.put("/me/profile", headers=headers, json=bad).status_code == 422
    assert client.get("/me/profile", headers=headers).json()["city"] == "Hyderabad"


# ---------------------------------------------------------------- memory, notes, reminders, context

def test_memory_view_edit_delete_and_agent_context(client):
    headers, body = signup(client)
    client.put("/me/settings", headers=headers, json={"tz": "Asia/Kolkata"})
    made = client.post("/memory", headers=headers, json={"text": "prefers tabs", "kind": "style"}).json()
    client.put(f"/memory/{made['id']}", headers=headers, json={"text": "prefers 4 spaces", "kind": "style"})
    with SessionLocal() as db:
        context = personal.context_for(db, body["user"]["id"])
    assert "prefers 4 spaces" in context and "Asia/Kolkata" in context
    assert client.delete(f"/memory/{made['id']}", headers=headers).json() == {"ok": True}
    assert client.get("/memory", headers=headers).json() == []
    assert client.put("/me/settings", headers=headers, json={"tz": "Mars/Base"}).status_code == 422


def test_voice_notes_are_tagged_and_searchable():
    uid = user_id()
    saved = run(pt.SaveNote(), uid, text="Call the dentist about the crown #health")
    assert saved.data["tags"][0] == "health" and "dentist" in saved.data["tags"]
    run(pt.SaveNote(), uid, text="Buy milk")
    found = run(pt.SearchNotes(), uid, query="dentist appointment")
    assert [n["text"] for n in found.data["notes"]] == ["Call the dentist about the crown #health"]


def test_reminders_respect_time_zone_and_repeat():
    uid = user_id()
    with SessionLocal() as db:
        personal.user_settings(db, uid).tz = "Asia/Kolkata"
        db.commit()
    result = run(pt.AddReminder(), uid, text="stand-up", when="2099-01-05T09:30", repeat="weekdays")
    assert "09:30" in result.summary
    with SessionLocal() as db:
        row = db.get(Reminder, result.data["id"])
        assert row.due_at == datetime(2099, 1, 5, 4, 0)  # 09:30 IST is 04:00 UTC
        friday = Reminder(user_id=uid, text="x", due_at=datetime(2099, 1, 9, 4, 0), repeat="weekdays")
        assert personal.next_due(friday, ZoneInfo("Asia/Kolkata")) == datetime(2099, 1, 12, 4, 0)  # skips the weekend
    with pytest.raises(ValueError, match="passed"):
        run(pt.AddReminder(), uid, text="late", when="2001-01-01T09:00")


def test_due_reminders_push_once_and_recurring_roll_forward(monkeypatch):
    from app import background

    uid = user_id()
    with SessionLocal() as db:
        db.add_all([Reminder(user_id=uid, text="once", due_at=datetime(2020, 1, 1)),
                    Reminder(user_id=uid, text="daily", due_at=datetime(2020, 1, 1), repeat="daily")])
        db.commit()
    sent = AsyncMock(return_value=1)
    monkeypatch.setattr(push, "notify", sent)
    assert asyncio.run(background.deliver_reminders()) >= 2
    with SessionLocal() as db:
        rows = {r.text: r for r in db.query(Reminder).filter(Reminder.user_id == uid)}
        assert rows["once"].done and not rows["daily"].done and rows["daily"].due_at.day == 2


def test_project_notes_section_append():
    uid = user_id()
    run(pt.UpdateProjectNotes(), uid, section="Decisions", text="SQLite for v1")
    run(pt.UpdateProjectNotes(), uid, section="To-dos", text="add login")
    from app import workspace as wsp
    with SessionLocal() as db:
        notes = (wsp.root(asyncio.run(wsp.current(db, uid))) / "NOTES.md").read_text()
    assert "## Decisions\n- SQLite for v1\n" in notes and "- [ ] add login" in notes


# ---------------------------------------------------------------- push encryption (decrypted like a browser would)

def test_a_subscription_with_unusable_keys_is_dropped_instead_of_failing_the_push(monkeypatch):
    from sqlalchemy import select

    from app.db import PushSubscription

    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings(), "vapid_private_key", push.b64(key.private_numbers().private_value.to_bytes(32, "big")))
    uid = user_id()
    with SessionLocal() as db:
        db.add(PushSubscription(user_id=uid, endpoint="https://push.example.com/x", p256dh="not-a-key", auth="nope"))
        db.commit()
    # raising here stopped the reminder job mid-loop, so one bad browser silenced everybody's reminders
    assert asyncio.run(push.notify(uid, "Reminder", "water the plants")) == 0
    with SessionLocal() as db:
        assert not db.scalars(select(PushSubscription).where(PushSubscription.user_id == uid)).all()


def test_web_push_payload_decrypts_like_a_browser(monkeypatch):
    browser = ec.generate_private_key(ec.SECP256R1())
    ua_public = push._raw_public(browser.public_key())
    auth = b"0123456789abcdef"
    body = push.encrypt(b'{"title":"hi"}', push.b64(ua_public), push.b64(auth))
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    shared = browser.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = push._hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    assert rs == 4096 and AESGCM(cek).decrypt(nonce, ciphertext, None) == b'{"title":"hi"}\x02'

    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings(), "vapid_private_key", push.b64(key.private_numbers().private_value.to_bytes(32, "big")))
    header = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc")
    token = header.split("t=")[1].split(",")[0]
    claims = json.loads(push.unb64(token.split(".")[1]))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] > time.time()


# ---------------------------------------------------------------- Google and GitHub against mocked APIs

def test_google_oauth_state_is_signed_and_tokens_refresh(monkeypatch):
    monkeypatch.setattr(settings(), "google_client_id", "cid")
    monkeypatch.setattr(settings(), "google_client_secret", "sec")
    uid = user_id()
    assert "state=" in google.connect_url(uid)
    with pytest.raises(google.GoogleError):
        google.read_state("forged.state.token", "google")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": f"at{len(calls)}", "refresh_token": "rt", "expires_in": 3600})
        assert request.headers["authorization"].startswith("Bearer at")
        return httpx.Response(200, json={"id": "msg1"})

    monkeypatch.setattr(google, "TRANSPORT", httpx.MockTransport(handler))
    with SessionLocal() as db:
        asyncio.run(google.finish_connect(db, "code", google.state_token(uid, "google")))
        assert load_secret(db, uid, "google")["refresh_token"] == "rt"
        assert asyncio.run(google.send_email(db, uid, ["a@example.com"], "Hi", "Body")) == "msg1"
    assert calls == ["/token", "/gmail/v1/users/me/messages/send"]


def test_github_pr_flow_uses_git_data_api(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        routes = {
            "/repos/o/r": {"default_branch": "main"},
            "/repos/o/r/git/ref/heads/main": {"object": {"sha": "base"}},
            "/repos/o/r/git/commits/base": {"tree": {"sha": "tree0"}},
            "/repos/o/r/git/trees": {"sha": "tree1"},
            "/repos/o/r/git/commits": {"sha": "c1"},
            "/repos/o/r/git/refs": {"ref": "refs/heads/feat"},
            "/repos/o/r/pulls": {"html_url": "https://github.com/o/r/pull/7"},
        }
        if path == "/repos/o/r/git/trees":
            assert json.loads(request.content)["base_tree"] == "tree0"
        return httpx.Response(201 if request.method == "POST" else 200, json=routes[path])

    monkeypatch.setattr(github, "TRANSPORT", httpx.MockTransport(handler))
    url = asyncio.run(github.Client("t").open_pr("o/r", "feat", "Rename", "", {"a.py": "x = 1\n"}))
    assert url == "https://github.com/o/r/pull/7"
    assert ("POST", "/repos/o/r/git/refs") in seen and ("POST", "/repos/o/r/pulls") in seen


def test_github_review_falls_back_when_lines_are_outside_the_diff(monkeypatch):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("comments"):
            return httpx.Response(422, json={"message": "line must be part of the diff"})
        return httpx.Response(200, json={"html_url": "https://github.com/o/r/pull/1#review"})

    monkeypatch.setattr(github, "TRANSPORT", httpx.MockTransport(handler))
    url = asyncio.run(github.Client("t").post_review("https://github.com/o/r/pull/1", "score 80",
                                                     [{"path": "a.py", "line": 3, "body": "bug"}]))
    assert url.endswith("#review") and "a.py:3" in bodies[-1]["body"]


def test_critical_integrations_need_explicit_yes():
    from app.skills import integration_tools as it
    assert {it.SendEmail.permission.value, it.PostReview.permission.value, it.OpenPullRequest.permission.value,
            it.PublishRepo.permission.value, it.Deploy.permission.value} == {"critical"}


# ---------------------------------------------------------------- payments

def test_stripe_signature_and_tier_flip(monkeypatch):
    uid = user_id()
    event = json.dumps({"type": "checkout.session.completed", "data": {"object": {"client_reference_id": uid}}}).encode()
    stamp = int(time.time())
    good = f"t={stamp},v1=" + hmac.new(b"whsec", f"{stamp}.".encode() + event, hashlib.sha256).hexdigest()
    billing.verify(event, good, "whsec")
    for bad in (good.replace("v1=", "v1=00"), f"t={stamp - 999},v1=x", ""):
        with pytest.raises(billing.BillingError):
            billing.verify(event, bad, "whsec")
    with SessionLocal() as db:
        assert billing.apply_event(db, json.loads(event)) == f"{uid} -> paid"
        assert db.get(User, uid).tier == "paid"


def test_stripe_webhook_is_refused_while_no_webhook_secret_is_set():
    event = json.dumps({"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "x"}}}).encode()
    stamp = int(time.time())
    forged = f"t={stamp},v1=" + hmac.new(b"", f"{stamp}.".encode() + event, hashlib.sha256).hexdigest()
    with pytest.raises(billing.BillingError):  # an empty secret is a key anyone can sign with
        billing.verify(event, forged, "")


# ---------------------------------------------------------------- bots

def test_bot_link_code_and_confirmation():
    uid = user_id()
    code = bots.new_link_code(uid)
    assert bots.link("telegram", "42", "WRONGCODE") is None
    assert bots.link("telegram", "42", code.lower()) is not None
    user, session_id = bots.linked("telegram", "42")
    assert user.id == uid and session_id
    assert bots.link("telegram", "43", code) is None  # codes work once

    replies = []

    async def reply(text):
        replies.append(text)

    async def flow():
        channel = bots.BotChannel(reply, reply)
        waiting = asyncio.create_task(channel.confirm({"skill": "send_email", "permission": "critical"}))
        await asyncio.sleep(0)
        assert channel.answer_confirm("Haan")
        assert await waiting == "yes"
        await channel.emit("answer.delta", text="Sent.")
        await channel.emit("answer.done", text="Sent.")

    asyncio.run(flow())
    assert replies == ["Allow send_email? (say yes, or no)", "Sent."]


def test_whatsapp_webhook_needs_a_valid_signature(client, monkeypatch):
    monkeypatch.setattr(settings(), "whatsapp_app_secret", "appsecret")
    monkeypatch.setattr(settings(), "whatsapp_verify_token", "verify")
    payload = json.dumps({"entry": []}).encode()
    sig = "sha256=" + hmac.new(b"appsecret", payload, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/whatsapp", content=payload, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    assert client.post("/webhooks/whatsapp", content=payload, headers={"x-hub-signature-256": sig}).json() == {"ok": True}
    challenge = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify",
                                                         "hub.challenge": "123"})
    assert challenge.text == "123"


def test_delete_account_removes_new_data(client):
    headers, body = signup(client)
    client.post("/memory", headers=headers, json={"text": "likes tea"})
    client.post("/notes", headers=headers, json={"text": "note"})
    client.put("/me/profile", headers=headers, json={"city": "Pune"})
    client.get("/workspaces", headers=headers)
    exported = client.get("/me/export", headers=headers).json()
    assert exported["memories"] and exported["notes"] and exported["form_profile"] == {"city": "Pune"}
    assert "profile" in exported["connected"]
    client.delete("/me", headers=headers)
    with SessionLocal() as db:
        assert load_secret(db, body["user"]["id"], "profile") is None


def _browser_keys():
    raw = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        push.serialization.Encoding.X962, push.serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": push.b64(raw), "auth": push.b64(b"0123456789abcdef")}


def test_push_subscribe_only_accepts_real_push_services_and_keys(client):
    headers, _ = signup(client)
    keys = _browser_keys()
    ok = client.post("/push/subscribe", headers=headers,
                     json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys})
    assert ok.status_code == 200
    for endpoint in ("https://169.254.169.254/latest", "https://evil.example/fcm.googleapis.com",
                     "https://fcm.googleapis.com.evil.example/x"):
        r = client.post("/push/subscribe", headers=headers, json={"endpoint": endpoint, "keys": keys})
        assert r.status_code == 422, endpoint
    bad = client.post("/push/subscribe", headers=headers, json={
        "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/x", "keys": {"p256dh": "short", "auth": "x"}})
    assert bad.status_code == 422


def test_whatsapp_webhook_runs_each_message_once_and_never_errors(client, monkeypatch):
    monkeypatch.setattr(settings(), "whatsapp_app_secret", "appsecret")
    seen = []

    async def failing(message):
        seen.append(message["id"])
        raise RuntimeError("download failed")

    monkeypatch.setattr(bots, "whatsapp_message", failing)
    payload = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.1", "from": "91"}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"appsecret", payload, hashlib.sha256).hexdigest()
    for _ in range(2):  # Meta redelivers the same message
        assert client.post("/webhooks/whatsapp", content=payload, headers={"x-hub-signature-256": sig}).status_code == 200
    deadline = time.monotonic() + 2
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert seen == ["wamid.1"]


def test_bot_turns_in_one_chat_run_one_at_a_time(monkeypatch):
    from app.api import ws

    order = []

    async def fake_turn(channel, user, session_id, text, *rest):
        order.append(f"start {text}")
        await asyncio.sleep(0.01)
        order.append(f"end {text}")

    monkeypatch.setattr(ws, "run_turn", fake_turn)

    async def flow():
        channel = bots.BotChannel(AsyncMock(), AsyncMock())
        await asyncio.gather(bots._run(channel, None, "s", "a", None), bots._run(channel, None, "s", "b", None))

    asyncio.run(flow())
    assert order == ["start a", "end a", "start b", "end b"]


def test_delete_account_removes_error_rows_and_live_chats(client):
    from app.api import ws
    from app.db import ErrorEvent, record_error

    headers, body = signup(client)
    uid = body["user"]["id"]
    chat = client.post("/sessions", headers=headers).json()["id"]
    ws._channels[chat] = ws.Channel()
    record_error("turn", "boom", uid)
    client.delete("/me", headers=headers)
    assert chat not in ws._channels
    with SessionLocal() as db:
        assert db.query(ErrorEvent).filter(ErrorEvent.user_id == uid).count() == 0
