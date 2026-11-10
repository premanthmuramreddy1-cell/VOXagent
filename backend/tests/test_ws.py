import asyncio
import json

import pytest

from app import providers as prov
from app.api import ws as ws_module
from app.config import settings
from app.providers import LLMEvent
from app.skills import Permission, discover
from tests.conftest import signup
from tests.fakes import EchoSkill, FakeLLM, FakeSTT, FakeTTS, call, plan


@pytest.fixture
def fake(monkeypatch):
    def use(*script, stt=None):
        llm, tts = FakeLLM(*script), FakeTTS()
        monkeypatch.setattr(prov, "providers", lambda: prov.Providers([llm], [stt or FakeSTT()], [tts]))
        return llm, tts

    return use


def open_session(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/sessions", headers=headers).json()["id"]
    return session_id, headers


def connect(client, session_id, token):
    sock = client.websocket_connect(f"/ws/sessions/{session_id}").__enter__()
    sock.send_json({"type": "auth", "token": token})
    assert sock.receive_json()["type"] == "ready"
    return sock


def collect(sock, stop="turn.done"):
    events, audio = [], []
    while True:
        message = sock.receive()
        if message.get("bytes"):
            audio.append(message["bytes"])
            continue
        events.append(json.loads(message["text"]))
        if events[-1]["type"] == stop:
            return events, audio


def types(events):
    return [e["type"] for e in events]


def test_text_turn_streams_answer_and_persists(client, token, fake):
    fake(plan(restatement="Greet me"), "Hello there.")
    session_id, headers = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "say hello"})
    events, audio = collect(sock)
    assert types(events) == ["agent.restate", "answer.delta", "answer.delta", "answer.done", "turn.done"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert audio == []  # speech was not requested
    saved = client.get(f"/sessions/{session_id}/messages", headers=headers).json()
    assert [(m["role"], m["text"]) for m in saved] == [("user", "say hello"), ("assistant", "Hello there.")]
    assert client.get("/sessions", headers=headers).json()[0]["title"] == "say hello"
    log = client.get(f"/sessions/{session_id}/activity", headers=headers).json()
    assert log[0]["action"] == "plan" and "Greet me" in log[0]["reason"]


def test_answer_is_spoken_sentence_by_sentence_in_the_users_language(client, token, fake):
    _, tts = fake(plan(language="hi"), "Namaste. Aap kaise hain?")
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "namaste bolo", "speak": True})
    _, audio = collect(sock)
    assert tts.spoken == [("Namaste.", "hi"), ("Aap kaise hain?", "hi")]
    assert len(audio) == 2 and audio[0].startswith(b"RIFF")


def test_code_blocks_are_shown_but_not_read_aloud(client, token, fake):
    _, tts = fake(plan(), "Here it is. ```python\nx = 1\n``` That is all.")
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "show me", "speak": True})
    events, _ = collect(sock)
    assert [text for text, _ in tts.spoken] == ["Here it is.", "That is all."]
    assert "x = 1" in events[-2]["text"]


def test_voice_turn_transcribes_and_normalizes_code_terms(client, token, fake):
    llm, _ = fake(plan(), "Going there.", stt=FakeSTT("go to line forty two", "en"))
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "audio.start"})
    sock.send_bytes(b"\x00\x01" * 8000)
    sock.send_json({"type": "audio.stop"})
    events, _ = collect(sock)
    assert events[0] == {"type": "transcript.final", "seq": 1, "text": "go to line 42", "language": "en"}
    assert llm.calls[0]["messages"][-1]["content"] == "go to line 42"


def test_unclear_speech_asks_instead_of_guessing(client, token, fake):
    llm, _ = fake(stt=FakeSTT("mumble", "en", confident=False))
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "audio.start"})
    sock.send_bytes(b"\x00\x01" * 100)
    sock.send_json({"type": "audio.stop", "speak": True})
    events, audio = collect(sock)
    assert types(events) == ["transcript.final", "clarify", "turn.done"]
    assert llm.calls == [] and len(audio) == 1


def test_socket_rejects_bad_token_and_other_users_session(client, token, fake):
    session_id, _ = open_session(client, token)
    for bad in ("nonsense", signup(client)[1]["access_token"]):
        with client.websocket_connect(f"/ws/sessions/{session_id}") as sock:
            sock.send_json({"type": "auth", "token": bad})
            assert sock.receive()["code"] == 4401


def test_reconnect_resumes_missed_events(client, token, fake):
    fake(plan(), "Done.")
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "hi"})
    first, _ = collect(sock)
    sock.close()
    again = connect(client, session_id, token)
    again.send_json({"type": "resume", "last_seq": 2})
    replay, _ = collect(again)
    assert replay == first[2:]


def test_llm_outage_reports_retryable_error(client, token, fake):
    fake(RuntimeError("ollama is down"))
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "hi"})
    events, _ = collect(sock)
    assert events[0]["code"] == "provider_failed" and events[0]["retryable"] is True


def test_barge_in_stops_the_agent(client, token, monkeypatch):
    class SlowLLM:
        name = "slow"

        async def stream(self, messages, tools=None, schema=None):
            if schema:
                yield LLMEvent(json.dumps(plan()))
                return
            yield LLMEvent("One moment. ")
            await asyncio.sleep(30)

    monkeypatch.setattr(prov, "providers", lambda: prov.Providers([SlowLLM()], [FakeSTT()], [FakeTTS()]))
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "talk for a while"})
    collect(sock, stop="answer.delta")
    sock.send_json({"type": "barge_in"})
    events, _ = collect(sock, stop="tts.stop")
    assert "answer.done" not in types(events)


def test_change_skill_waits_for_apply(client, token, fake, monkeypatch):
    risky = EchoSkill("risky", Permission.CHANGE)
    monkeypatch.setitem(discover(), "risky", risky)
    fake(plan([("Change it", "risky")]), call("risky"), "Applied.")
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "change it"})
    events, _ = collect(sock, stop="confirm.request")
    assert events[-1]["permission"] == "change" and risky.runs == 0
    sock.send_json({"type": "confirm", "id": events[-1]["id"], "decision": "apply"})
    events, _ = collect(sock)
    assert "tool.finished" in types(events) and risky.runs == 1


def test_free_tier_daily_message_limit(client, token, fake, monkeypatch):
    fake()
    monkeypatch.setattr(settings(), "free_daily_messages", 0)
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "text.input", "text": "hi"})
    events, _ = collect(sock)
    assert events[0]["code"] == "limit"


def test_oversized_audio_and_malformed_messages_are_rejected(client, token, fake, monkeypatch):
    fake()
    monkeypatch.setattr(settings(), "max_audio_s", 1)
    session_id, _ = open_session(client, token)
    sock = connect(client, session_id, token)
    sock.send_json({"type": "audio.start"})
    sock.send_bytes(b"\x00" * (ws_module.BYTES_PER_S + 2))
    assert sock.receive_json()["code"] == "audio_too_long"
    sock.send_text("not json")
    assert sock.receive_json()["code"] == "bad_message"
    sock.send_json({"type": "nope"})
    assert sock.receive_json()["code"] == "bad_message"
