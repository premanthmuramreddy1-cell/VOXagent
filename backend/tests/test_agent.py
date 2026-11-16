import asyncio

import pytest
from sqlalchemy import select

from app.agent import Agent
from app.db import Activity, ErrorEvent, SessionLocal
from app.providers import ProviderError, ToolCall, first_ok, llm_stream
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
    llm = FakeLLM(plan([("Echo it", "echo"), ("Echo again", "echo")]), call(text="hi"), "I echoed hi.")
    rec = Recorder()
    answer, final_plan = run(Agent([llm], {"echo": echo}), rec)
    assert answer == "I echoed hi." and echo.runs == 1
    assert rec.types() == [
        "agent.restate", "plan.created", "tool.started", "tool.finished", "plan.step", "plan.step",
        "answer.delta", "answer.delta",
    ]
    assert all(step["done"] for step in final_plan["steps"])
    assert llm.calls[1]["tools"][0]["function"]["name"] == "echo"
    assert llm.calls[2]["tools"] is None  # the skill already ran
    assert llm.calls[2]["messages"][-1]["content"] == "<data>echoed hi</data>"  # tool output is fenced as data


def test_agent_keeps_tool_call_id_and_signature_in_history():
    meta = {"id": "a", "extra_content": {"google": {"thought_signature": "sig"}}}
    llm = FakeLLM(plan([("Echo it", "echo")]), [ToolCall("echo", {"text": "hi"}, meta=meta)], "done")
    run(Agent([llm], {"echo": EchoSkill()}), Recorder())
    sent = next(m for m in llm.calls[2]["messages"] if m.get("tool_calls"))["tool_calls"][0]
    assert sent["id"] == "a" and sent["extra_content"] == meta["extra_content"]


def test_talk_only_plan_steps_are_never_shown_as_done_work():
    llm = FakeLLM(plan([("Acknowledge the request", ""), ("Explain that I cannot browse", "")]), "Hello.")
    rec = Recorder()
    _, final_plan = run(Agent([llm], {"echo": EchoSkill()}), rec)
    assert final_plan["steps"] == [] and "plan.created" not in rec.types() and "plan.step" not in rec.types()


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
    assert "nope is not available" in llm.calls[2]["messages"][-1]["content"]
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


@pytest.mark.parametrize(("said", "read", "accepted"), [
    ("crete a websit for my cofee shop", "create a website for my coffee shop", True),
    ("fix teh bugg in main.py", "fix the bug in main.py", True),
    ("make a website", "make a website", False),  # nothing to fix
    ("order pizza", "order a large pepperoni pizza from dominos", False),  # adds meaning: a guess, not a fix
    ("delete my files", "keep my files", False),  # changes meaning
    ("hello", "", False),
])
def test_spelling_fixes_are_accepted_but_guesses_are_not(said, read, accepted):
    from app.agent import is_spelling_fix
    assert is_spelling_fix(said, read) is accepted


def test_misspelled_request_is_read_as_fixed_and_shown_to_the_user():
    understood = {**plan(restatement="Make a coffee shop site"), "understood": "make a website for my coffee shop"}
    llm = FakeLLM(understood, "On it.")
    rec = Recorder()
    run(Agent([llm], {}), rec, text="mak a websit for my cofee shop")
    assert ("agent.understood", {"original": "mak a websit for my cofee shop",
                                 "text": "make a website for my coffee shop"}) in rec.events
    assert llm.calls[1]["messages"][-1]["content"].endswith("(Read as, with spelling fixed: make a website for my "
                                                             "coffee shop)")


def test_planner_guess_beyond_spelling_is_ignored():
    guessed = {**plan(), "understood": "order a large pepperoni pizza from dominos"}
    llm = FakeLLM(guessed, "Sure.")
    rec = Recorder()
    run(Agent([llm], {}), rec, text="order pizza")
    assert "agent.understood" not in rec.types()
    assert llm.calls[1]["messages"][-1]["content"] == "order pizza"


def test_planner_sees_recent_conversation_to_resolve_references():
    llm = FakeLLM(plan(), "Again.")
    history = [{"role": "user", "content": "make the header blue"}, {"role": "assistant", "content": "Done."}]
    run(Agent([llm], {}), Recorder(), text="do that again", history=history)
    assert [m["content"] for m in llm.calls[0]["messages"][1:]] == ["make the header blue", "Done.", "do that again"]


def test_final_skill_result_is_spoken_as_written_without_another_model_call():
    class Briefing(EchoSkill):
        async def run(self, args, ctx):
            from app.skills import SkillResult
            return SkillResult("Due soon: Rent today (₹15,000).", final=True)

    llm = FakeLLM(plan([("Brief", "echo")]), call(text="x"), "7 am: drink water. 9 am: review finances.")
    rec = Recorder()
    answer, _ = run(Agent([llm], {"echo": Briefing()}), rec)
    assert answer == "Due soon: Rent today (₹15,000)."  # no invented schedule
    assert len(llm.calls) == 2  # planner + the tool-calling step only


def test_final_result_does_not_end_the_turn_while_another_planned_skill_is_waiting():
    from app.skills import SkillResult

    class Briefing(EchoSkill):
        async def run(self, args, ctx):
            self.runs += 1
            return SkillResult("Due soon: Rent today.", final=True)

    brief, echo = Briefing("brief"), EchoSkill()
    llm = FakeLLM(plan([("Brief", "brief"), ("Echo", "echo")]), call("brief", text="x"), call(text="milk"),
                  "Morning: a light breakfast. 4 pm: a short walk.")
    answer, final_plan = run(Agent([llm], {"brief": brief, "echo": echo}), Recorder())
    assert brief.runs == 1 and echo.runs == 1  # the second skill was not skipped
    assert answer == "Due soon: Rent today. echoed milk"  # both tools' own words, no model padding after them
    assert len(llm.calls) == 3 and all(s["done"] for s in final_plan["steps"])


def test_shortlist_matches_typos_and_plurals_without_a_phrase_list():
    from app.agent import shortlist
    from app.skills import discover

    skills = discover()
    assert "list_add" in shortlist("add milk and bread to my grocery list", skills)
    assert "list_show" in shortlist("whats on my shoping list", skills)
    assert "add_reminder" in shortlist("remind me to call mom at 6pm", skills)
    assert "world_briefing" in shortlist("any news on cricket?", skills)
    assert shortlist("hi", skills) == []
    assert shortlist("what is 17 times 23", skills) == []  # one chance word in a description is not a match
    assert "generate_code" not in shortlist("what is in my readme", skills)


def test_empty_plan_offers_the_shortlist_once_and_a_plain_answer_needs_no_tool():
    echo = EchoSkill()
    llm = FakeLLM(plan(), call(text="hi"), "Done.")
    answer, _ = run(Agent([llm], {"echo": echo}), Recorder(), text="please repeat this text back")
    assert echo.runs == 1 and llm.calls[1]["tools"][0]["function"]["name"] == "echo"
    assert llm.calls[2]["tools"] is None  # one offer, then no more tools
    chat = FakeLLM(plan(), "Paris.")
    answer, _ = run(Agent([chat], {"echo": EchoSkill()}), Recorder(), text="capital of france")
    assert answer == "Paris." and chat.calls[1]["tools"] is None  # nothing matched, nothing offered


def test_a_tool_the_model_was_not_offered_never_runs():
    echo, other = EchoSkill(), EchoSkill("other")
    llm = FakeLLM(plan([("Echo", "echo")]), call("other", text="x"), "ok")
    run(Agent([llm], {"echo": echo, "other": other}), Recorder())
    assert other.runs == 0 and "other is not available" in llm.calls[2]["messages"][-1]["content"]


@pytest.mark.parametrize(("text", "guess", "want"), [
    ("how is the stock market doing today", "hi", "en"), ("mujhe ek rocket banake dikhao", "hi", "hi"),
    ("namaste bolo", "hi", "hi"), ("aaj ka news batao", "hi", "hi"),
    ("नमस्ते", "hi", "hi"), ("hello", "en", "en"),
])
def test_latin_text_is_only_hindi_when_it_reads_like_hinglish(text, guess, want):
    llm = FakeLLM(plan(language=guess), "ok")
    _, final = run(Agent([llm], {}), Recorder(), text=text)
    assert final["language"] == want


def test_an_empty_answer_with_no_tool_run_never_claims_done():
    answer, _ = run(Agent([FakeLLM(plan(), "")], {}), Recorder(), text="asdfghjkl")
    assert answer != "Done." and "another way" in answer


@pytest.mark.parametrize(("decision", "sent"), [("yes", 1), ("no", 0)])
def test_a_filled_form_asks_to_submit_in_the_same_turn(decision, sent):
    from pydantic import BaseModel

    from app.skills import SkillResult

    class Nothing(BaseModel):
        pass

    class Fill:
        name, description, input_schema, permission = "fill", "Fill a form.", Nothing, Permission.READ

        async def run(self, args, ctx):
            return SkillResult("I filled 3 field(s).", then="send")

    class Send:
        name, description, input_schema, permission = "send", "Submit it.", Nothing, Permission.CRITICAL
        runs = 0

        async def run(self, args, ctx):
            Send.runs += 1
            return SkillResult("Submitted.")

    recorder = Recorder(decision)
    llm = FakeLLM(plan([("Fill", "fill")]), call("fill"), "ok")
    run(Agent([llm], {"fill": Fill(), "send": Send()}), recorder, text="fill this form")
    assert [c["skill"] for c in recorder.confirms] == ["send"]  # the yes is asked now, not left for a later turn
    assert Send.runs == sent


def test_a_shared_link_is_opened_before_the_model_answers():
    from pydantic import BaseModel

    from app.skills import SkillResult

    class Url(BaseModel):
        url: str

    class Fetch:
        name, description, input_schema, permission = "web_fetch", "Open a page.", Url, Permission.READ
        opened: list[str] = []

        async def run(self, args, ctx):
            Fetch.opened.append(args.url)
            return SkillResult("Kochi Metro: 25 stations, opened 2017.")

    llm = FakeLLM(plan(), "It has 25 stations.")
    run(Agent([llm], {"web_fetch": Fetch()}), Recorder(),
        text="how many stations does this have? https://en.wikipedia.org/wiki/Kochi_Metro.")
    assert Fetch.opened == ["https://en.wikipedia.org/wiki/Kochi_Metro"]  # trailing full stop is not the link
    assert "25 stations, opened 2017" in llm.calls[1]["messages"][0]["content"]


def test_a_form_link_goes_to_the_form_tool_unread():
    from pydantic import BaseModel

    from app.skills import SkillResult

    class Url(BaseModel):
        url: str

    class Tool:
        input_schema, permission = Url, Permission.READ
        opened: list[str] = []

        def __init__(self, name, description):
            self.name, self.description = name, description

        async def run(self, args, ctx):
            Tool.opened.append(self.name)
            return SkillResult("I answered 2 of 2 question(s).")

    skills = {"web_fetch": Tool("web_fetch", "Open a page."),
              "fill_form": Tool("fill_form", "Fill any web form or quiz: 'fill this form', 'answer this quiz'.")}
    llm = FakeLLM(plan(), call("fill_form", url="https://forms.example/q"), "Done.")
    run(Agent([llm], skills), Recorder(), text="answer the questions in this quiz https://forms.example/q")
    assert Tool.opened == ["fill_form"]  # read as plain text, a scripted form has no questions to show the model
    assert "fill_form" in llm.calls[1]["messages"][0]["content"]  # the model is told the tool fits


def test_a_form_service_link_is_filled_even_when_the_model_would_refuse():
    from pydantic import BaseModel

    from app.skills import SkillResult

    class Url(BaseModel):
        url: str

    class NoArgs(BaseModel):
        pass

    ran: list[tuple[str, dict]] = []

    class Fill:
        name, description, input_schema, permission = "fill_form", "Fill a form.", Url, Permission.CHANGE

        async def run(self, args, ctx):
            ran.append(("fill_form", args.model_dump()))
            return SkillResult("I answered 2 of 2 question(s).", then="submit_form")

    class Submit:
        name, description, input_schema, permission = "submit_form", "Submit it.", NoArgs, Permission.CRITICAL

        async def run(self, args, ctx):
            ran.append(("submit_form", {}))
            return SkillResult("Submitted.")

    llm = FakeLLM(plan())  # the model itself never calls a tool
    answer, _ = run(Agent([llm], {"fill_form": Fill(), "submit_form": Submit()}), Recorder(decision="yes"),
                    text="fill this form https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=abc.")
    assert ran == [("fill_form", {"url": "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=abc"}),
                   ("submit_form", {})]
    assert answer == "I answered 2 of 2 question(s).\nSubmitted."  # read out as the tools reported it
    assert len(llm.calls) == 1  # only the planner: the model never rewords what happened to the form


def test_submit_it_with_a_filled_form_waiting_offers_only_submit(monkeypatch):
    from app import forms

    monkeypatch.setitem(forms.SESSIONS, "u1", object())  # a form filled earlier is waiting for the user's yes
    llm = FakeLLM(plan(), "OK.")
    skills = {"submit_form": EchoSkill("submit_form"), "screen_control": EchoSkill("screen_control")}
    run(Agent([llm], skills), Recorder(), text="ok submit it")
    assert [t["function"]["name"] for t in llm.calls[1]["tools"]] == ["submit_form"]


def test_an_echoed_data_tag_never_reaches_the_user():
    answer, _ = run(Agent([FakeLLM(plan(), "<data>FastAPI is fast.</data>")], {}), Recorder(), text="summarize it")
    assert answer == "FastAPI is fast."
