from sqlalchemy import select

from app.db import BotLink, Message, SessionLocal
from tests.conftest import signup


def new_session(client, headers):
    return client.post("/sessions", headers=headers).json()["id"]


def test_rename_session_trims_the_name(client):
    headers, _ = signup(client)
    sid = new_session(client, headers)
    r = client.patch(f"/sessions/{sid}", headers=headers, json={"title": "  Trip   plans "})
    assert r.status_code == 200 and r.json()["title"] == "Trip plans"
    assert client.get("/sessions", headers=headers).json()[0]["title"] == "Trip plans"


def test_rename_session_rejects_a_blank_name(client):
    headers, _ = signup(client)
    sid = new_session(client, headers)
    assert client.patch(f"/sessions/{sid}", headers=headers, json={"title": "   "}).status_code == 422
    assert client.patch(f"/sessions/{sid}", headers=headers, json={"title": "x" * 81}).status_code == 422


def test_delete_session_removes_it_and_its_messages(client):
    headers, _ = signup(client)
    sid = new_session(client, headers)
    with SessionLocal() as db:
        db.add(Message(session_id=sid, role="user", text="hello"))
        db.commit()
    assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
    assert client.get("/sessions", headers=headers).json() == []
    assert client.get(f"/sessions/{sid}/messages", headers=headers).status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(Message).where(Message.session_id == sid)).all() == []


def test_another_users_session_cannot_be_renamed_or_deleted(client):
    owner, _ = signup(client)
    other, _ = signup(client)
    sid = new_session(client, owner)
    assert client.patch(f"/sessions/{sid}", headers=other, json={"title": "mine"}).status_code == 404
    assert client.delete(f"/sessions/{sid}", headers=other).status_code == 404
    assert client.get(f"/sessions/{sid}/messages", headers=owner).status_code == 200


def test_deleting_a_bot_linked_session_gives_the_bot_a_fresh_one(client):
    headers, body = signup(client)
    sid = new_session(client, headers)
    with SessionLocal() as db:
        db.add(BotLink(user_id=body["user"]["id"], platform="telegram", external_id="42", session_id=sid))
        db.commit()
    assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
    with SessionLocal() as db:
        link = db.scalars(select(BotLink).where(BotLink.external_id == "42")).one()
        assert link.session_id and link.session_id != sid
    assert [s["title"] for s in client.get("/sessions", headers=headers).json()] == ["Telegram chat"]
