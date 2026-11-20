import asyncio

import pytest
from sqlalchemy import select

from app.agent import Agent
from app.db import Activity, ErrorEvent, SessionLocal
from app.providers import ProviderError, ToolCall, first_ok, llm_stream
from app.skills import Permission, SkillResult, discover, web_tools
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


def test_agent_answers_when_the_plan_leaves_out_optional_fields():
    partial = {"restatement": "Say hello", "language": "en", "steps": []}  # small models drop "unclear"/"question"
    answer, _ = run(Agent([FakeLLM(partial, "Hello there.")], {}), Recorder())
    assert answer == "Hello there."


def test_agent_runs_planned_skill_and_ticks_checklist():
    echo = EchoSkill()
    llm = FakeLLM(plan([("Echo it", "echo"), ("Echo again", "echo")]), call(text="hi"), "I echoed hi.")
    rec = Recorder()
    answer, final_plan = run(Agent([llm], {"echo": echo}), rec)
    assert answer == "I echoed hi." and echo.runs == 1
    assert rec.types() == [
        "agent.restate", "plan.created", "tool.started", "tool.finished", "plan.step", "plan.step",
        "answer.delta",  # the reply after a tool is held, checked against its output, then sent in one piece
    ]
    assert all(step["done"] for step in final_plan["steps"])
    assert llm.calls[1]["tools"][0]["function"]["name"] == "echo"
    assert llm.calls[2]["tools"] is None  # the skill already ran
    assert llm.calls[2]["messages"][-1]["content"] == "<data>echoed hi</data>"  # tool output is fenced as data


def test_a_planned_skill_is_forced_on_the_first_round_only(monkeypatch):
    import app.agent as agent_mod

    forced = []

    def spy(llms, messages, tools=None, schema=None, force_tool=False):
        forced.append(force_tool)
        return llm_stream(llms, messages, tools, schema)

    monkeypatch.setattr(agent_mod, "llm_stream", spy)
    run(Agent([FakeLLM(plan([("Echo it", "echo")]), call(text="hi"), "done")], {"echo": EchoSkill()}), Recorder())
    run(Agent([FakeLLM(plan(), "Just chatting.")], {"echo": EchoSkill()}), Recorder())
    # plan, forced call, free answer, self-check against the tool output; then plan, chat (no tool: no check)
    assert forced == [False, True, False, False, False, False]


def test_an_answer_the_tool_output_contradicts_is_corrected_before_it_is_final():
    fix = {"ok": False, "problems": ["the echo said hi"], "fixed": "The echo said hi."}
    llm = FakeLLM(plan([("Echo it", "echo")]), call(text="hi"), "The echo said bye.", fix)
    rec = Recorder()
    answer, _ = run(Agent([llm], {"echo": EchoSkill()}), rec)
    assert answer == "The echo said hi."
    said = "".join(p["text"] for t, p in rec.events if t == "answer.delta")
    assert said == "The echo said hi."  # the wrong version was never shown, so never spoken
    assert "echoed hi" in llm.calls[3]["messages"][1]["content"]  # checked against what the tool returned


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


class AskingSkill(EchoSkill):
    """A skill that asks the model itself, as code_review and fix_and_run do."""

    async def run(self, args, ctx):
        return SkillResult(await ctx.complete([{"role": "user", "content": args.text}]), {})


@pytest.mark.parametrize("skill, coder_asked", [("code_review", True), ("echo", False)])
def test_coding_skills_ask_the_code_models_and_others_the_main_ones(skill, coder_asked):
    main = FakeLLM(plan([("Do it", skill)]), call(skill, text="x"), "main reply", "done")
    coder = FakeLLM("coder reply")
    run(Agent([main], {skill: AskingSkill(skill)}, [coder]), Recorder())
    assert bool(coder.calls) is coder_asked
    # plan, tool call, [skill's own question], answer, self-check of the answer against the tool output
    assert len(main.calls) == (4 if coder_asked else 5)


def test_without_code_models_coding_skills_use_the_main_chain():
    main = FakeLLM(plan([("Review", "code_review")]), call("code_review", text="x"), "review", "done")
    run(Agent([main], {"code_review": AskingSkill("code_review")}), Recorder())
    assert len(main.calls) == 5  # plan, tool call, the skill's question, answer, self-check


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


def test_owner_only_skills_are_refused_for_others_before_any_confirmation(monkeypatch):
    import app.agent as agent_mod

    skill = EchoSkill("mac", Permission.CHANGE)
    skill.owner_only = True
    rec = Recorder("apply")
    monkeypatch.setattr(agent_mod, "is_owner", lambda user_id: False)
    run(Agent([FakeLLM(plan([("Do it", "mac")]), call("mac"), "ok")], {"mac": skill}), rec)
    assert skill.runs == 0 and rec.confirms == [] and activity("mac") == ["not owner"]


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


def test_a_reply_cut_off_at_the_output_limit_is_written_again_by_the_next_model():
    from app.providers import LLMEvent

    class Model:
        def __init__(self, name, text, cut_off):
            self.name, self.text, self.cut_off, self.calls = name, text, cut_off, 0

        async def stream(self, messages, tools=None, schema=None):
            self.calls += 1
            yield LLMEvent(self.text)
            yield LLMEvent(cut_off=self.cut_off)

    short, full = Model("short", "<html><body>half", True), Model("full", "<html>whole</html>", False)
    text = asyncio.run(Agent([short, full], {}).complete("u1", [{"role": "user", "content": "write a page"}]))
    assert text == "<html>whole</html>" and short.calls == 1 and full.calls == 1


def test_attached_file_is_named_to_the_model_and_explain_is_offered():
    # the model once guessed "add.py" for an attached calc.py and called read_file, which only sees the workspace
    from app.db import Upload, User
    with SessionLocal() as db:
        db.add(User(id="u1", email="u1@example.com", password_hash="x"))
        db.add(Upload(id="up1", user_id="u1", filename="calc.py", size=10))
        db.commit()
    llm = FakeLLM(plan(), "It adds two numbers.")
    run(Agent([llm], {"explain": EchoSkill("explain")}), Recorder(), text="what does it return?", upload_ids=["up1"])
    planner_said, model_said = llm.calls[0]["messages"][-1]["content"], llm.calls[1]["messages"][-1]["content"]
    for said in (planner_said, model_said):
        assert "calc.py" in said and "explain" in said
    assert "explain" in [t["function"]["name"] if "function" in t else t["name"] for t in llm.calls[1]["tools"]]


class FakeSearch:
    name, input_schema, permission = "web_search", web_tools.SearchArgs, Permission.READ
    description = "Search the web."

    def __init__(self):
        self.queries = []

    async def run(self, args, ctx):
        self.queries.append(args.query)
        return SkillResult("Tokyo has about 14 million people (https://stat.example/tokyo)",
                           {"results": [{"title": "Tokyo stats", "url": "https://stat.example/tokyo", "snippet": ""}]})


def test_a_factual_question_is_answered_from_a_web_search_with_its_sources():
    search = FakeSearch()
    llm = FakeLLM({**plan(restatement="Tokyo population"), "factual": True}, "About 14 million [stat.example].",
                  {"ok": True, "problems": [], "fixed": ""})
    rec = Recorder()
    answer, _ = run(Agent([llm], {"web_search": search}), rec)
    assert search.queries and answer == "About 14 million [stat.example]."
    finished = next(p for t, p in rec.events if t == "tool.finished")
    assert finished["data"]["results"][0]["url"] == "https://stat.example/tokyo"  # shown under the answer as a source
    assert "14 million people" in llm.calls[1]["messages"][0]["content"]  # the answer was written from the search


def test_chat_and_tasks_are_not_sent_to_the_web():
    search = FakeSearch()
    run(Agent([FakeLLM(plan(), "Hi!")], {"web_search": search}), Recorder())
    assert search.queries == []


def test_a_plain_world_question_is_searched_even_when_the_planner_misses_it():
    search = FakeSearch()
    llm = FakeLLM(plan(), "About 14 million [Tokyo stats](https://stat.example/tokyo).",
                  {"ok": True, "problems": [], "fixed": ""})
    run(Agent([llm], {"web_search": search}), Recorder(), text="How many people live in Tokyo today?")
    assert search.queries  # planner said not factual; the plain question still got a source


def test_questions_about_the_user_are_not_sent_to_the_web():
    search = FakeSearch()
    run(Agent([FakeLLM(plan(), "Your name is Asha.")], {"web_search": search}), Recorder(), text="What is my name?")
    assert search.queries == []


def test_a_learned_preference_is_an_instruction_in_every_reply():
    from app.db import Memory, User, new_id

    with SessionLocal() as db:
        user = User(email=f"p{new_id()}@example.com", password_hash="x")
        db.add(user)
        db.flush()
        db.add(Memory(user_id=user.id, kind="preference", text="wants to be called Captain"))
        db.commit()
        uid = user.id
    llm = FakeLLM(plan(), "Aye, Captain.")
    run(Agent([llm], {}), Recorder(), user_id=uid, text="hello there")
    system = llm.calls[1]["messages"][0]["content"]
    assert "Follow them in every reply" in system and "- wants to be called Captain" in system
    assert "<data>" not in system.split("standing preferences")[1]  # outside the data wrapper


def test_a_question_about_what_the_user_said_before_searches_their_history():
    class FakeRecall:
        name, input_schema, permission = "recall", web_tools.SearchArgs, Permission.READ
        description = "Search history."
        queries: list = []

        async def run(self, args, ctx):
            self.queries.append(args.query)
            return SkillResult("[chat] marinate paneer in yogurt", {"hits": []})

    recall = FakeRecall()
    unclear = {**plan(), "unclear": True, "question": "Which cooking thing?"}
    llm = FakeLLM(unclear, "You told me to marinate paneer in yogurt.")
    answer, _ = run(Agent([llm], {"recall": recall}), Recorder(), text="What was that cooking thing I told you about?")
    assert recall.queries and "paneer" in answer  # looked it up instead of asking the user to repeat it


def test_bracket_citations_become_links_the_screen_can_open():
    search = FakeSearch()
    llm = FakeLLM({**plan(), "factual": True}, "Big【Tokyo stats】(https://stat.example/tokyo) and 【https://b.example/x】.",
                  {"ok": True, "problems": [], "fixed": ""})
    answer, _ = run(Agent([llm], {"web_search": search}), Recorder(), text="How big is Tokyo?")
    assert answer == "Big[Tokyo stats](https://stat.example/tokyo) and [source](https://b.example/x)."


def test_the_shown_reply_and_the_saved_answer_match_after_link_fixing():
    llm = FakeLLM({**plan(), "factual": True}, "Big 【https://b.example/x】.", {"ok": True, "problems": [], "fixed": ""})
    rec = Recorder()
    answer, _ = run(Agent([llm], {"web_search": FakeSearch()}), rec, text="How big is Tokyo?")
    shown = "".join(p["text"] for t, p in rec.events if t == "answer.delta")
    assert shown == answer == "Big [source](https://b.example/x)."


def test_small_talk_is_never_sent_to_the_web_even_if_the_planner_says_factual():
    search = FakeSearch()
    run(Agent([FakeLLM({**plan(), "factual": True}, "You're welcome!")], {"web_search": search}), Recorder(),
        text="thanks!")
    assert search.queries == []


def test_an_offered_news_tool_does_not_replace_the_search_for_a_fact():
    class FakeBriefing(FakeSearch):
        name = "world_briefing"
        description = "Population news and headlines for India and other countries."  # gets it shortlisted

    search, briefing = FakeSearch(), FakeBriefing()
    llm = FakeLLM({**plan(), "factual": True}, "About 1.45 billion.", {"ok": True, "problems": [], "fixed": ""})
    run(Agent([llm], {"web_search": search, "world_briefing": briefing}), Recorder(),
        text="What is the population of India?")
    assert search.queries and briefing.queries == []
    assert all(t["function"]["name"] != "world_briefing" for t in llm.calls[1]["tools"] or [])


def _recall_skill():
    class FakeRecall:
        name, input_schema, permission = "recall", web_tools.SearchArgs, Permission.READ
        description = "Search history."

        def __init__(self):
            self.queries = []

        async def run(self, args, ctx):
            self.queries.append(args.query)
            return SkillResult("[chat] Priya's wedding is on 14 February", {"hits": []})

    return FakeRecall()


def test_an_unclear_question_without_a_cue_looks_in_the_history_before_asking():
    recall = _recall_skill()
    unclear = {**plan(), "unclear": True, "question": "Whose function?"}
    llm = FakeLLM(unclear, "Priya's wedding, on 14 February.")
    rec = Recorder()
    answer, _ = run(Agent([llm], {"recall": recall}), rec, text="whose family function is coming up?")
    assert recall.queries and "Priya" in answer and not any(t == "clarify" for t, _ in rec.events)


def test_a_personal_question_checks_the_history_before_the_web():
    recall, search = _recall_skill(), FakeSearch()
    llm = FakeLLM({**plan(), "factual": True}, "Renew it in January.")
    answer, _ = run(Agent([llm], {"recall": recall, "web_search": search}), Recorder(),
                    text="When do I need to renew my passport?")
    assert recall.queries and search.queries == [] and answer == "Renew it in January."


def test_a_plain_unclear_command_still_asks():
    recall = _recall_skill()
    rec = Recorder()
    run(Agent([FakeLLM({**plan(), "unclear": True, "question": "Book what?"})], {"recall": recall}), rec, text="book it")
    assert recall.queries == [] and any(t == "clarify" for t, _ in rec.events)


def test_code_tools_and_a_linkless_fetch_are_dropped_for_a_general_question():
    search = FakeSearch()
    planned = {**plan(), "steps": [{"text": "x", "skill": "web_fetch"}, {"text": "y", "skill": "ask_project"}]}
    llm = FakeLLM(planned, "Built by Shah Jahan.", {"ok": True, "problems": [], "fixed": ""})
    skills = {"web_search": search, "web_fetch": FakeSearch(), "ask_project": FakeSearch()}
    _, used = run(Agent([llm], skills), Recorder(), text="tell me about the Taj Mahal")
    assert search.queries and used["steps"] == []  # the fact was searched, not the project


def test_a_first_person_question_checks_memory_even_when_another_tool_is_planned():
    recall = _recall_skill()
    planned = {**plan(), "steps": [{"text": "x", "skill": "list_payments"}]}

    class FakePayments(FakeSearch):
        name = "list_payments"

    llm = FakeLLM(planned, call("list_payments", query="groceries"), "11,000 in November.")
    run(Agent([llm], {"recall": recall, "list_payments": FakePayments()}), Recorder(),
        text="how much did I spend on groceries last month?")
    assert recall.queries


def test_a_source_named_without_a_link_is_left_as_plain_text():
    llm = FakeLLM({**plan(), "factual": True}, "828 m【Burj Khalifa - Wikipedia】.", {"ok": True, "problems": [], "fixed": ""})
    answer, _ = run(Agent([llm], {"web_search": FakeSearch()}), Recorder(), text="How tall is the Burj Khalifa?")
    assert answer == "828 m (Burj Khalifa - Wikipedia)."


def test_a_question_about_the_users_code_goes_to_the_code_tools_not_memory():
    recall = _recall_skill()
    run(Agent([FakeLLM(plan(), "It runs turns.")], {"recall": recall}), Recorder(),
        text="what does app/agent.py do in my project?")
    assert recall.queries == []


def test_a_general_fact_is_searched_even_when_the_planner_picks_memory():
    search = FakeSearch()
    planned = {**plan(), "steps": [{"text": "x", "skill": "recall"}]}
    llm = FakeLLM(planned, call("recall", query="Taj Mahal"), "A marble tomb in Agra.", {"ok": True, "problems": [], "fixed": ""})
    run(Agent([llm], {"recall": _recall_skill(), "web_search": search}), Recorder(), text="tell me about the Taj Mahal")
    assert search.queries


def test_a_general_fact_is_searched_whatever_non_source_tool_the_planner_picked():
    search = FakeSearch()

    class FakeNotes(FakeSearch):
        name = "list_notes"

    planned = {**plan(), "steps": [{"text": "x", "skill": "list_notes"}]}
    llm = FakeLLM(planned, call("list_notes", query="x"), "Plates slipping.", {"ok": True, "problems": [], "fixed": ""})
    run(Agent([llm], {"web_search": search, "list_notes": FakeNotes()}), Recorder(), text="what causes earthquakes")
    assert search.queries


def test_the_memory_search_is_told_todays_date():
    llm = FakeLLM(plan(), "October: 9,000.")
    run(Agent([llm], {"recall": _recall_skill()}), Recorder(), text="how much did I spend on groceries last month?",
        context="Today is Wednesday 18 November 2026, 22:30.\nmore")
    assert "files were searched for this (Today is Wednesday 18 November 2026" in llm.calls[1]["messages"][0]["content"]


def test_a_maths_question_with_a_maths_tool_is_not_web_searched():
    search = FakeSearch()

    class FakeCalc(FakeSearch):
        name = "calculate"

    planned = {**plan(), "steps": [{"text": "x", "skill": "calculate"}]}
    llm = FakeLLM(planned, call("calculate", query="0.17*2340"), "397.8", {"ok": True, "problems": [], "fixed": ""})
    run(Agent([llm], {"web_search": search, "calculate": FakeCalc()}), Recorder(), text="What is 17% of 2,340?")
    assert search.queries == []


def test_latex_delimiters_are_removed_from_the_answer():
    from app.agent import _links
    assert _links(r"The roots are \(x = 2\) and \[x = 3\].") == "The roots are x = 2 and x = 3."
