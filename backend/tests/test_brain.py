import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from app import brain, web
from app.config import settings
from app.db import ChatSession, Memory, Message, Note, SessionLocal, User, new_id
from app.skills import RunContext
from app.skills import brain_tools as bt


def user() -> str:
    with SessionLocal() as db:
        row = User(email=f"b{new_id()}@example.com", password_hash="x")
        db.add(row)
        db.commit()
        return row.id


def scripted(*replies: str):
    """A complete() that returns the given replies in order and records what it was asked."""
    asked: list[list[dict]] = []

    async def complete(messages, schema=None):
        asked.append(messages)
        return replies[len(asked) - 1]
    return complete, asked


def test_check_returns_the_fixed_answer_when_the_evidence_disagrees():
    complete, _ = scripted(json.dumps({"ok": False, "problems": ["wrong year"], "fixed": "It opened in 1931."}))
    assert asyncio.run(brain.check(complete, "When?", "It opened in 1913.", "opened 1931")) == "It opened in 1931."


def test_check_keeps_the_answer_when_fine_broken_or_without_evidence():
    fine, _ = scripted(json.dumps({"ok": True, "problems": [], "fixed": ""}))
    broken, _ = scripted("not json")
    unused, asked = scripted()
    assert asyncio.run(brain.check(fine, "q", "answer", "evidence")) == "answer"
    assert asyncio.run(brain.check(broken, "q", "answer", "evidence")) == "answer"
    assert asyncio.run(brain.check(unused, "q", "answer", "")) == "answer"
    assert asked == []  # nothing to check against: no model call


def test_learn_saves_new_preferences_once_and_skips_secrets_and_small_talk():
    uid = user()
    reply = json.dumps({"preferences": ["prefers short answers", "uses Celsius", "password is hunter2"]})
    complete, asked = scripted(reply, reply)
    assert asyncio.run(brain.learn(complete, uid, "what's the weather")) == []
    assert asked == []  # no preference cue: the model is not asked
    assert asyncio.run(brain.learn(complete, uid, "I prefer short answers, and use celsius")) == [
        "prefers short answers", "uses Celsius"]
    assert asyncio.run(brain.learn(complete, uid, "Keep it short from now on")) == []  # already known
    with SessionLocal() as db:
        kept = sorted(m.text for m in db.scalars(select(Memory).where(Memory.user_id == uid)))
    assert kept == ["prefers short answers", "uses Celsius"]


def test_recall_finds_past_chats_notes_and_files_of_this_user_only():
    uid, other = user(), user()
    with SessionLocal() as db:
        mine = ChatSession(user_id=uid, title="Dinner")
        theirs = ChatSession(user_id=other, title="Theirs")
        db.add_all([mine, theirs])
        db.flush()
        db.add_all([Message(session_id=mine.id, role="user", text="Save my paneer tikka recipe: yogurt, spices"),
                    Message(session_id=theirs.id, role="user", text="secret paneer plan"),
                    Note(user_id=uid, text="buy paneer on friday")])
        db.commit()
    folder = Path(settings().data_dir) / "uploads" / uid / new_id()
    folder.mkdir(parents=True)
    (folder / "menu.md").write_text("Sunday: paneer butter masala")
    hits = bt.recall(uid, "paneer recipe")
    sources = " ".join(h["source"] for h in hits)
    assert "chat “Dinner”" in sources and "note" in sources and "menu.md" in sources
    assert "secret" not in json.dumps(hits)  # another user's chat never leaks
    assert hits[0]["text"].startswith("Save my paneer tikka recipe")  # the best match (both words) comes first


def test_deep_research_reads_many_sources_cites_them_and_is_checked(monkeypatch):
    async def search(query, limit=6):
        return [{"title": f"{query} {i}", "url": f"https://e.com/{abs(hash(query)) % 997}/{i}", "snippet": "s"}
                for i in range(limit)]

    async def fetch(url, max_chars=20_000):
        if url.endswith("/0"):
            raise ValueError("blocked")  # a dead page still counts through its snippet
        return {"url": url, "title": "T", "text": f"text of {url}"}

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "fetch", fetch)
    queries = json.dumps({"queries": [f"angle {i}" for i in range(6)]})
    checked = json.dumps({"ok": False, "problems": ["x"], "fixed": "Corrected report [1]."})
    complete, asked = scripted(queries, "Draft report [1].", checked)
    ctx = RunContext("u", "s", "", [], AsyncMock(), complete)
    result = asyncio.run(bt.DeepResearch().run(bt.ResearchArgs(question="Is X good?"), ctx))
    assert len(result.data["sources"]) >= 20
    assert result.summary.startswith("Corrected report [1].") and result.final
    assert "[20]" in asked[1][1]["content"]  # the writer saw every numbered source


def test_agent_team_runs_the_parts_in_parallel_then_merges_and_checks(monkeypatch):
    running, most = 0, 0

    async def search(query, limit=6):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [{"title": query, "url": f"https://e.com/{query}", "snippet": "fact"}]

    monkeypatch.setattr(web, "search", search)
    parts = json.dumps({"parts": [{"role": "Researcher", "job": "find flights", "web": True},
                                  {"role": "Planner", "job": "find hotels", "web": True}]})
    ok = json.dumps({"ok": True, "problems": [], "fixed": ""})
    complete, asked = scripted(parts, "flights part", "hotels part", "Merged plan", ok)
    ctx = RunContext("u", "s", "", [], AsyncMock(), complete)
    result = asyncio.run(bt.AgentTeam().run(bt.TeamArgs(task="Plan Goa trip"), ctx))
    assert most == 2  # both teammates searched at the same time
    assert result.summary == "Merged plan" and result.final
    assert [m["role"] for m in result.data["team"]] == ["Researcher", "Planner"]
    assert {s["url"] for s in result.data["sources"]} == {"https://e.com/find flights", "https://e.com/find hotels"}
    assert "## Researcher\nflights part" in asked[3][1]["content"]
