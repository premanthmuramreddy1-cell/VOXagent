import asyncio

import pytest
from sqlalchemy import select

from app.agent import Agent
from app.db import Activity, ErrorEvent, SessionLocal
from app.providers import ProviderError, first_ok, llm_stream
from app.skills import Permission, discover
from app.spoken import normalize
from tests.fakes import EchoSkill, FakeLLM, Recorder, call, plan


def run(agent, rec, **kw):
    return asyncio.run(agent.run(rec.turn(**kw)))


def activity(action):
    with SessionLocal() as db:
        return [a.outcome for a in db.scalars(select(Activity).where(Activity.action == action))]


def test_agent_restates_then_answers_plain_question():
    rec = Recorder()
    answer, _ = run(Agent([FakeLLM(plan(restatement="Say hello"), "Hello there.")], {}), rec)
    assert answer == "Hello there."
    assert rec.events[0] == ("agent.restate", {"text": "Say hello", "language": "en"})
    assert "plan.created" not in rec.types()  # a one-liner needs no checklist
    assert "".join(p["text"] for t, p in rec.events if t == "answer.delta") == "Hello there."


def test_agent_runs_planned_skill_and_ticks_checklist():
    echo = EchoSkill()
    llm = FakeLLM(plan([("Echo it", "echo"), ("Report back", "")]), call(text="hi"), "I echoed hi.")
    rec = Recorder()
    answer, final_plan = run(Agent([llm], {"echo": echo}), rec)
    assert answer == "I echoed hi." and echo.runs == 1
    assert rec.types() == [
        "agent.restate", "plan.created", "tool.started", "tool.finished", "plan.step",
        "answer.delta", "answer.delta", "plan.step",
    ]
    assert all(step["done"] for step in final_plan["steps"])
    assert llm.calls[1]["tools"][0]["function"]["name"] == "echo"
    assert llm.calls[2]["tools"] is None  # the skill already ran
    assert llm.calls[2]["messages"][-1]["content"] == "<data>echoed hi</data>"  # tool output is fenced as data


def test_agent_only_offers_skills_the_plan_asked_for():
    llm = FakeLLM(plan(), "Just chatting.")
    run(Agent([llm], {"echo": EchoSkill()}), Recorder())
    assert llm.calls[1]["tools"] is None


@pytest.mark.parametrize(
    ("permission", "decision", "runs"),
    [
        (Permission.CHANGE, "no", 0),
        (Permission.CHANGE, "apply", 1),
        (Permission.CRITICAL, "apply", 0),  # "apply" is not enough to send, pay or deploy
        (Permission.CRITICAL, "yes", 1),
    ],
)
def test_confirmation_gate(permission, decision, runs):
    name = f"risky-{permission.value}-{decision}"
    skill = EchoSkill(name, permission)
    rec = Recorder(decision)
    run(Agent([FakeLLM(plan([("Do it", name)]), call(name), "ok")], {name: skill}), rec)
    assert skill.runs == runs
    assert rec.confirms[0]["permission"] == permission.value
    assert activity(name) == ["ok" if runs else "declined"]


def test_read_skill_runs_without_confirmation():
    rec = Recorder()
    run(Agent([FakeLLM(plan([("Echo", "echo")]), call(), "ok")], {"echo": EchoSkill()}), rec)
    assert rec.confirms == []


def test_unclear_request_gets_a_clarifying_question():
    rec = Recorder()
    llm = FakeLLM(plan(unclear=True, question="Which file do you mean?"))
    answer, _ = run(Agent([llm], {}), rec)
    assert answer == "Which file do you mean?"
    assert rec.types() == ["clarify"]


def test_broken_plan_json_falls_back_to_plain_answer():
    rec = Recorder()
    answer, final_plan = run(Agent([FakeLLM("not json at all", "Fine.")], {}), rec, text="hello")
    assert answer == "Fine." and final_plan["restatement"] == "hello"


def test_speech_language_wins_and_unknown_language_defaults_to_english():
    llm = FakeLLM(plan(language="fr"), "ok")
    run(Agent([llm], {}), Recorder())
    assert "Reply in English" in llm.calls[1]["messages"][0]["content"]
    llm = FakeLLM(plan(language="en"), "ok")
    run(Agent([llm], {}), Recorder(), language="te")
    assert "Reply in Telugu" in llm.calls[1]["messages"][0]["content"]


def test_failing_skill_is_reported_not_fatal():
    rec = Recorder()
    llm = FakeLLM(plan([("Echo", "echo")]), call(), "It failed, sorry.")
    answer, _ = run(Agent([llm], {"echo": EchoSkill(fail=True)}), rec)
    assert answer == "It failed, sorry."
    assert ("tool.finished", {"skill": "echo", "ok": False, "error": "boom"}) in rec.events


def test_unknown_skill_and_bad_arguments_go_back_to_the_model():
    llm = FakeLLM(plan([("Echo", "echo")]), call("nope"), call(text=["not", "a", "string"]), "ok")
    run(Agent([llm], {"echo": EchoSkill()}), Recorder())
    assert "no skill called nope" in llm.calls[2]["messages"][-1]["content"]
    assert "Invalid arguments" in llm.calls[3]["messages"][-1]["content"]


def test_llm_falls_back_to_second_provider_and_records_the_error():
    async def collect():
        return "".join([e.text async for e in llm_stream([FakeLLM(RuntimeError("down")), FakeLLM("backup")], [])])

    assert asyncio.run(collect()) == "backup"
    with SessionLocal() as db:
        assert db.scalar(select(ErrorEvent).where(ErrorEvent.message == "fake-llm: down"))


def test_all_providers_failing_raises_provider_error():
    async def boom(_provider):
        raise RuntimeError("down")

    with pytest.raises(ProviderError):
        asyncio.run(first_ok("tts", [FakeLLM(), FakeLLM()], boom))


def test_registry_discovers_skills_from_the_skills_folder():
    assert "code_review" in discover()


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        ("print open bracket x close bracket", "print ( x )"),
        ("go to line forty two", "go to line 42"),
        ("delete line one hundred and seven", "delete line 107"),
        ("rename it to camel case get user name", "rename it to getUserName"),
        ("call snake case fetch user in main", "call fetch_user in main"),
        ("what is the weather", "what is the weather"),
    ],
)
def test_spoken_code_terms(spoken, written):
    assert normalize(spoken) == written
