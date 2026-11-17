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
        db.add(BotLink(user_id=body["user"]["id"], platform="telegram", external_id=f"qa-{sid}", session_id=sid))
        db.commit()
    assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
    with SessionLocal() as db:
        link = db.scalars(select(BotLink).where(BotLink.external_id == f"qa-{sid}")).one()
        assert link.session_id and link.session_id != sid
    assert [s["title"] for s in client.get("/sessions", headers=headers).json()] == ["Telegram chat"]


def test_search_matches_titles_and_message_text_of_own_sessions_only(client):
    headers, _ = signup(client)
    other, _ = signup(client)
    trip, notes, theirs = new_session(client, headers), new_session(client, headers), new_session(client, other)
    client.patch(f"/sessions/{trip}", headers=headers, json={"title": "Goa trip"})
    with SessionLocal() as db:
        db.add(Message(session_id=notes, role="assistant", text="Book the GOA ferry at 7"))
        db.add(Message(session_id=theirs, role="user", text="goa"))
        db.commit()
    ids = lambda q: {s["id"] for s in client.get("/sessions", headers=headers, params={"q": q}).json()}
    assert ids("goa") == {trip, notes}
    assert ids("  ") == {trip, notes}  # blank search lists everything
    assert ids("100%") == set() and ids("_") == set()  # LIKE wildcards are matched literally


def test_export_session_as_markdown(client):
    headers, _ = signup(client)
    sid = new_session(client, headers)
    client.patch(f"/sessions/{sid}", headers=headers, json={"title": "Plans"})
    with SessionLocal() as db:
        db.add(Message(session_id=sid, role="user", text="hi"))
        db.add(Message(session_id=sid, role="assistant", text="- one\n- two"))
        db.commit()
    r = client.get(f"/sessions/{sid}/export", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Plans") and "**You:**\n\nhi" in r.text and "**VoxAgent:**\n\n- one\n- two" in r.text
    other, _ = signup(client)
    assert client.get(f"/sessions/{sid}/export", headers=other).status_code == 404
