import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.providers import CloudLLM, ToolCall, _build, _retry_wait


def sse(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def collect(provider, messages, **kw):
    async def run():
        return [event async for event in provider.stream(messages, **kw)]

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def cloud_settings(monkeypatch):
    monkeypatch.setattr(settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(settings(), "llm_base_url", "https://llm.example.com/v4")
    return settings()


def test_cloud_streams_text_and_reads_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " there."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 42}},
        ))

    provider = CloudLLM("glm-4.5-flash", transport=httpx.MockTransport(handler))
    events = collect(provider, [{"role": "user", "content": "hi"}])
    assert "".join(e.text for e in events) == "Hello there."
    assert events[-1].tokens == 42
    assert seen["url"] == "https://llm.example.com/v4/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "glm-4.5-flash" and seen["body"]["stream"] is True


def test_cloud_accumulates_streamed_tool_call_arguments():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "type": "function",
                 "function": {"name": "shopping", "arguments": '{"query":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ' "mouse", "quantity": 2}'}}]}}]},
        ))

    provider = CloudLLM("m", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "shopping", "parameters": {}}}]
    events = collect(provider, [{"role": "user", "content": "buy"}], tools=tools)

    assert events[-1].tool_calls == [ToolCall("shopping", {"query": "mouse", "quantity": 2})]


def test_cloud_uses_json_mode_and_translates_tool_history():
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "{}"}}]}))

    history = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "buy"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "shopping", "arguments": {"query": "mouse"}}}]},
        {"role": "tool", "tool_name": "shopping", "content": "<data>ok</data>"},
    ]
    collect(CloudLLM("m", transport=httpx.MockTransport(handler)), history, schema={"type": "object"})

    assert sent["response_format"] == {"type": "json_object"}
    notes = [m for m in sent["messages"] if m["role"] == "system" and "JSON Schema" in m["content"]]
    assert len(notes) == 1 and sent["messages"][0]["content"] == "be brief"  # persona stays first
    assistant = next(m for m in sent["messages"] if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["type"] == "function"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "mouse"}'
    tool = next(m for m in sent["messages"] if m["role"] == "tool")
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]


def test_cloud_requires_a_key(monkeypatch):
    monkeypatch.setattr(settings(), "llm_api_key", "")
    with pytest.raises(RuntimeError, match="VOX_LLM_API_KEY"):
        collect(CloudLLM("m"), [{"role": "user", "content": "hi"}])


def test_cloud_reports_provider_errors_without_retrying():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(RuntimeError, match="401"):
        collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "hi"}])
    assert len(calls) == 1  # a bad key is not a transient failure


def test_cloud_retries_a_rate_limited_request(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    async def instant(_seconds: float) -> None: ...

    monkeypatch.setattr(asyncio, "sleep", instant)
    events = collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "hi"}])
    assert "".join(e.text for e in events) == "ok"
    assert len(calls) == 3


def test_cloud_gives_up_after_bounded_retries_and_backs_off(monkeypatch):
    calls, slept = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "down"}})

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    with pytest.raises(RuntimeError, match="503"):
        collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "hi"}])
    assert len(calls) == 3 and slept == [1.0, 2.0]


def test_retry_after_is_honoured_but_capped():
    assert _retry_wait("2", 1.0) == 2.0
    assert _retry_wait("3600", 1.0) == 10.0
    assert _retry_wait("soon", 1.0) == 1.0
    assert _retry_wait(None, 2.0) == 2.0


def test_cloud_provider_spec_is_wired():
    built = _build("cloud:glm-4.5-flash,ollama:llama3.2")
    assert [p.name for p in built] == ["cloud:glm-4.5-flash", "ollama:llama3.2"]


STRAY = {"error": {"message": "Tool choice is none, but model called a tool", "code": "tool_use_failed"}}


def test_cloud_retries_once_as_plain_text_when_the_model_calls_a_tool_that_was_not_offered():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, content=sse(STRAY))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "Here you go."}}]}))

    events = collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "hi"}])
    assert "".join(e.text for e in events) == "Here you go."
    assert len(bodies) == 2 and "tools" not in bodies[1]
    assert bodies[1]["messages"][-1]["content"].startswith("No tools are available")


def test_cloud_gives_up_after_one_plain_text_retry_and_never_retries_when_tools_were_offered():
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(200, content=sse(STRAY))

    provider = CloudLLM("m", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="tool_use_failed"):
        collect(provider, [{"role": "user", "content": "hi"}])
    assert len(hits) == 2  # the first try and one retry, not a loop
    hits.clear()
    tool = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]
    with pytest.raises(RuntimeError, match="tool_use_failed"):
        collect(provider, [{"role": "user", "content": "hi"}], tools=tool)
    assert len(hits) == 1
