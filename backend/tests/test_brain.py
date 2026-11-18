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


def test_recall_finds_what_was_said_in_other_words():
    uid = user()
    with SessionLocal() as db:
        chat = ChatSession(user_id=uid, title="Food")
        db.add(chat)
        db.flush()
        db.add(Message(session_id=chat.id, role="user", text="Marinate paneer in yogurt and grill it"))
        db.commit()
    assert bt.recall(uid, "cooking thing") == []  # no word in common
    complete, asked = scripted(json.dumps({"words": ["recipe", "marinate", "grill"]}), json.dumps({"items": []}))
    ctx = RunContext(uid, "s", "", [], AsyncMock(), complete)
    result = asyncio.run(bt.Recall().run(bt.RecallArgs(query="cooking thing"), ctx))
    assert result.data["hits"][0]["text"].startswith("Marinate paneer")
    assert asked  # the model supplied the related words


def test_a_report_keeps_only_citations_to_sources_it_read():
    report = "EVs are cheaper [1, 2, 30]. Fuel costs more [31]. Tax is 5% [4]. Range [2-3]."
    assert bt.cited_only(report, 24) == "EVs are cheaper [1, 2]. Fuel costs more. Tax is 5% [4]. Range [2, 3]."


def test_a_report_without_citations_is_rewritten_once_with_them(monkeypatch):
    async def search(query, limit=6):
        return [{"title": f"t{i}", "url": f"https://e.com/{i}", "snippet": "s"} for i in range(3)]

    async def fetch(url, max_chars=20_000):
        return {"url": url, "title": "T", "text": "nuclear deaths per TWh 0.03"}

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "fetch", fetch)
    ok = json.dumps({"ok": True, "problems": [], "fixed": ""})
    complete, asked = scripted(json.dumps({"queries": []}), "Nuclear is safe.", ok, "Nuclear is safe [1].")
    ctx = RunContext("u", "s", "", [], AsyncMock(), complete)
    result = asyncio.run(bt.DeepResearch().run(bt.ResearchArgs(question="Is nuclear safe?"), ctx))
    assert result.summary.startswith("Nuclear is safe [1].")
    assert "Add a [n] source number" in asked[3][1]["content"]  # after the check, which may also drop them


def test_recall_still_finds_a_match_when_the_model_query_and_expansion_fail():
    uid = user()
    with SessionLocal() as db:
        chat = ChatSession(user_id=uid, title="Food")
        db.add(chat)
        db.flush()
        db.add(Message(session_id=chat.id, role="user", text="My paneer marinade uses yogurt"))
        db.commit()
    complete, _ = scripted("not json", "not json")
    ctx = RunContext(uid, "s", "what was my paneer marinade again?", [], AsyncMock(), complete)
    result = asyncio.run(bt.Recall().run(bt.RecallArgs(query="cooking note"), ctx))
    assert result.data["hits"] and "paneer" in result.data["hits"][0]["text"]


def test_recall_by_meaning_finds_a_message_that_shares_no_words():
    uid = user()
    with SessionLocal() as db:
        chat = ChatSession(user_id=uid, title="Food")
        db.add(chat)
        db.flush()
        db.add_all([Message(session_id=chat.id, role="user", text="Marinate paneer in yogurt, then grill it"),
                    Message(session_id=chat.id, role="user", text="Book a cab to the airport")])
        db.commit()
    order = [item["text"] for item in bt._recent(uid)]
    picks = json.dumps({"items": [order.index("Marinate paneer in yogurt, then grill it")]})
    complete, asked = scripted("not json", picks)  # word expansion failed; the model reads and picks by meaning
    ctx = RunContext(uid, "s", "what was that cooking thing I told you about?", [], AsyncMock(), complete)
    result = asyncio.run(bt.Recall().run(bt.RecallArgs(query="cooking thing"), ctx))
    assert [h["text"] for h in result.data["hits"]] == ["Marinate paneer in yogurt, then grill it"]
    assert "Book a cab" in asked[1][1]["content"]  # it saw the history as data and chose


def test_learn_drops_fragments_that_are_not_a_preference():
    uid = user()
    complete, _ = scripted(json.dumps({"preferences": ["Captain", "wants to be called Captain"]}))
    assert asyncio.run(brain.learn(complete, uid, "Call me Captain from now on")) == ["wants to be called Captain"]


def test_learn_keeps_the_users_own_words_when_the_model_returns_only_fragments():
    uid = user()
    complete, _ = scripted(json.dumps({"preferences": ["concise", "brief"]}))
    saved = asyncio.run(brain.learn(complete, uid, "Keep your answers under 25 words from now on."))
    assert saved == ['in their words: "Keep your answers under 25 words from now on."']
