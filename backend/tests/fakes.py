import json

from pydantic import BaseModel

from app.agent import Turn
from app.providers import LLMEvent, ToolCall, Transcript
from app.skills import Permission, SkillResult


def plan(steps=(), restatement="Do the thing", language="en", unclear=False, question=""):
    return {
        "restatement": restatement, "language": language, "unclear": unclear, "question": question,
        "steps": [{"text": text, "skill": skill} for text, skill in steps],
    }


class FakeLLM:
    """Replies from a script: a str or dict is text, a list is tool calls, an Exception is raised."""

    name = "fake-llm"

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    async def stream(self, messages, tools=None, schema=None):
        self.calls.append({"messages": list(messages), "tools": tools, "schema": schema})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            yield LLMEvent(tool_calls=item, tokens=7)
            return
        text = json.dumps(item) if isinstance(item, dict) else item
        half = len(text) // 2
        yield LLMEvent(text[:half])
        yield LLMEvent(text[half:], tokens=7)


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text="hello there", language="en", confident=True):
        self.result = Transcript(text, language, confident)

    async def transcribe(self, pcm, language=None):
        return self.result


class FakeTTS:
    name = "fake-tts"

    def __init__(self):
        self.spoken = []

    async def synthesize(self, text, language):
        self.spoken.append((text, language))
        return b"RIFF" + text.encode()


class EchoArgs(BaseModel):
    text: str = ""


class EchoSkill:
    description = "Repeats text back."
    input_schema = EchoArgs

    def __init__(self, name="echo", permission=Permission.READ, fail=False):
        self.name, self.permission, self.fail, self.runs = name, permission, fail, 0

    async def run(self, args, ctx):
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")
        return SkillResult(f"echoed {args.text}", {"text": args.text})


def call(name="echo", **arguments):
    return [ToolCall(name, arguments)]


class Recorder:
    """Collects emitted events and answers confirmation requests."""

    def __init__(self, decision="no"):
        self.events, self.decision, self.confirms = [], decision, []

    async def emit(self, type, **payload):
        self.events.append((type, payload))

    async def confirm(self, request):
        self.confirms.append(request)
        return self.decision

    def types(self):
        return [t for t, _ in self.events]

    def turn(self, text="please do it", user_id="u1", **kw):
        return Turn(user_id, "s1", text, self.emit, self.confirm, kw.pop("upload_ids", []), [], **kw)
