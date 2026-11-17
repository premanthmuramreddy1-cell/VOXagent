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


def test_cloud_keeps_parallel_tool_calls_apart_when_index_is_missing():
    # Gemini's OpenAI-compatible stream sends each call whole, with an id but no index.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "weather", "arguments": '{"city": "Paris"}'}},
                {"id": "b", "type": "function", "function": {"name": "time_in", "arguments": '{"city": "Paris"}'}},
            ]}}]},
        ))

    provider = CloudLLM("gemini-3.1-flash-lite", transport=httpx.MockTransport(handler))
    events = collect(provider, [{"role": "user", "content": "weather and time"}], tools=[{"type": "function"}])

    assert events[-1].tool_calls == [ToolCall("weather", {"city": "Paris"}), ToolCall("time_in", {"city": "Paris"})]


SIG = {"google": {"thought_signature": "sig-abc"}}


def test_cloud_keeps_gemini_thought_signature_with_the_call():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse({"choices": [{"delta": {"tool_calls": [
            {"id": "a", "type": "function", "extra_content": SIG,
             "function": {"name": "weather", "arguments": '{"city": "Paris"}'}}]}}]}))

    events = collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "w"}],
                     tools=[{"type": "function"}])
    assert events[-1].tool_calls[0].meta == {"id": "a", "extra_content": SIG}


def test_history_sends_the_thought_signature_back():
    # Gemini 3 rejects the next step with "Function call is missing a thought_signature" without this.
    from app.providers import _openai_messages

    out = _openai_messages([
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "extra_content": SIG, "function": {"name": "weather", "arguments": {"city": "Paris"}}}]},
        {"role": "tool", "content": "sunny"},
    ])
    assert out[1]["tool_calls"][0]["id"] == "a" and out[1]["tool_calls"][0]["extra_content"] == SIG
    assert out[2]["tool_call_id"] == "a"


def test_cloud_asks_for_usage_in_the_stream():
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    collect(CloudLLM("m", transport=httpx.MockTransport(handler)), [{"role": "user", "content": "hi"}])
    assert sent["stream_options"] == {"include_usage": True}  # Gemini omits usage (and cost) without it


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


@pytest.mark.parametrize(("headers", "body"), [
    ({"retry-after": "1191"}, '{"error": {"message": "slow down"}}'),
    ({}, '{"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}}'),
])
def test_cloud_out_of_quota_fails_fast_so_the_local_model_answers(monkeypatch, headers, body):
    import app.providers as prov
    from app.providers import llm_stream

    sleeps, hits = [], []
    monkeypatch.setattr(prov.asyncio, "sleep", lambda s: sleeps.append(s) or asyncio.sleep(0))

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(429, headers=headers, content=body.encode())

    class Local:
        name = "ollama:test"

        async def stream(self, messages, tools=None, schema=None):
            from app.providers import LLMEvent
            yield LLMEvent("from the local model")

    async def run():
        return [e async for e in llm_stream([CloudLLM("m", transport=httpx.MockTransport(handler)), Local()],
                                            [{"role": "user", "content": "hi"}])]

    assert "".join(e.text for e in asyncio.run(run())) == "from the local model"
    assert len(hits) == 1 and sleeps == []  # no retries and no waiting before the fallback


def test_tool_calls_written_as_text_become_real_calls():
    from app.providers import text_tool_call

    names = {"list_add", "add_habit"}
    good = text_tool_call('{"name": "list_add", "parameters": {"items": ["milk"]}}', names)
    assert good == ToolCall("list_add", {"items": ["milk"]})
    broken = text_tool_call('{"name":"add_habit","parameters{"}}', names)  # what llama3.2 really wrote
    assert broken == ToolCall("add_habit", {})
    assert text_tool_call('{"name": "rm_rf", "parameters": {}}', names) is None  # not offered: not a call
    assert text_tool_call("{ not json and no name", names) is None


def test_ollama_holds_back_a_text_tool_call_instead_of_showing_it(monkeypatch):
    from app.providers import OllamaLLM

    lines = [{"message": {"content": '{"name":"list_add",'}}, {"message": {"content": '"parameters{"}}'}},
             {"message": {"content": ""}, "done": True, "eval_count": 9}]
    body = "\n".join(json.dumps(x) for x in lines).encode()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body)), **kw))
    tool = [{"type": "function", "function": {"name": "list_add", "parameters": {}}}]
    events = collect(OllamaLLM("llama3.2"), [{"role": "user", "content": "add milk"}], tools=tool)
    assert "".join(e.text for e in events) == ""  # the user never sees the raw call
    assert [c.name for e in events for c in e.tool_calls] == ["list_add"]
    plain = collect(OllamaLLM("llama3.2"), [{"role": "user", "content": "hi"}])  # no tools offered: text as is
    assert "".join(e.text for e in plain) == '{"name":"list_add","parameters{"}}'


def test_ollama_drops_bare_json_that_is_not_a_tool_call(monkeypatch):
    from app.providers import OllamaLLM

    body = "\n".join(json.dumps(x) for x in [{"message": {"content": "{}"}}, {"done": True}]).encode()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body)), **kw))
    tool = [{"type": "function", "function": {"name": "world_briefing", "parameters": {}}}]
    events = collect(OllamaLLM("llama3.2"), [{"role": "user", "content": "latest news about AI"}], tools=tool)
    assert "".join(e.text for e in events) == ""  # llama3.2 answered '{}' and the user saw it


def test_a_provider_slow_to_start_hands_the_turn_to_the_next(monkeypatch):
    from app import config
    from app.providers import LLMEvent, llm_stream

    monkeypatch.setattr(config.settings(), "llm_first_token_s", 0.05)

    class Slow:
        name = "cloud:slow"

        async def stream(self, messages, tools=None, schema=None):
            await asyncio.sleep(5)  # Gemini under load: 8-46 s before the first token
            yield LLMEvent("too late", [], 0)

    class Fast:
        name = "ollama:fast"

        async def stream(self, messages, tools=None, schema=None):
            yield LLMEvent("here", [], 0)

    async def go():
        return "".join([e.text async for e in llm_stream([Slow(), Fast()], [])])

    assert asyncio.run(asyncio.wait_for(go(), 2)) == "here"



def test_a_cloud_without_its_key_fails_fast_so_the_next_model_answers(monkeypatch):
    monkeypatch.setattr(settings(), "groq_api_key", "")
    with pytest.raises(RuntimeError, match="VOX_GROQ_API_KEY"):
        collect(CloudLLM("openai/gpt-oss-20b", vendor="groq"), [{"role": "user", "content": "hi"}])

def test_groq_models_keep_the_slash_in_their_name_and_use_the_groq_key(monkeypatch):
    monkeypatch.setattr(settings(), "groq_api_key", "groq-key")
    monkeypatch.setattr(settings(), "groq_base_url", "https://groq.example.com/openai/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    (groq,) = _build("groq:qwen/qwen3.8-27b")
    groq.transport = httpx.MockTransport(handler)
    collect(groq, [{"role": "user", "content": "hi"}])
    assert seen == {"url": "https://groq.example.com/openai/v1/chat/completions", "auth": "Bearer groq-key",
                    "model": "qwen/qwen3.8-27b"}


def test_a_throttled_model_with_another_behind_it_hands_over_without_waiting(monkeypatch):
    monkeypatch.setattr(settings(), "groq_api_key", "groq-key")
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(429, headers={"retry-after": "8"}, content=b'{"error": "rate limit: tokens per minute"}')

    first, last = _build("groq:qwen/qwen3.8-27b,groq:openai/gpt-oss-20b")
    assert first.fail_fast and not last.fail_fast  # the last model still waits and retries: nothing comes after it
    first.transport = httpx.MockTransport(handler)
    with pytest.raises(RuntimeError, match="429"):
        collect(first, [{"role": "user", "content": "hi"}])
    assert len(hits) == 1


def test_forcing_a_tool_makes_the_cloud_model_call_one_and_leaves_local_models_on_auto():
    from app.providers import LLMEvent, llm_stream

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    seen: list[dict] = []

    class Local:
        name = "ollama:test"

        async def stream(self, messages, tools=None, schema=None):  # no tool_choice: forcing must not reach it
            seen.append({"tools": tools})
            yield LLMEvent("ok")

    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]

    async def run(candidates, force):
        return [e async for e in llm_stream(candidates, [{"role": "user", "content": "hi"}], tools, force_tool=force)]

    cloud = CloudLLM("m", transport=httpx.MockTransport(handler))
    asyncio.run(run([cloud], True))
    asyncio.run(run([cloud], False))
    asyncio.run(run([Local()], True))
    assert [b["tool_choice"] for b in sent] == ["required", "auto"]
    assert seen == [{"tools": tools}]


def test_meta_models_use_the_meta_key_and_llama_api(monkeypatch):
    monkeypatch.setattr(settings(), "meta_api_key", "LLM|1|meta-key")
    monkeypatch.setattr(settings(), "meta_base_url", "https://llama.example.com/compat/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    (meta,) = _build("meta:Llama-4-Maverick-17B-128E-Instruct-FP8")
    meta.transport = httpx.MockTransport(handler)
    collect(meta, [{"role": "user", "content": "hi"}])
    assert seen == {"url": "https://llama.example.com/compat/v1/chat/completions", "auth": "Bearer LLM|1|meta-key",
                    "model": "Llama-4-Maverick-17B-128E-Instruct-FP8"}


def test_openrouter_models_keep_their_free_suffix_and_use_the_openrouter_key(monkeypatch):
    monkeypatch.setattr(settings(), "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings(), "openrouter_base_url", "https://or.example.com/api/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    (router,) = _build("openrouter:nvidia/nemotron-3.5-super-vl:free")
    router.transport = httpx.MockTransport(handler)
    collect(router, [{"role": "user", "content": "hi"}])
    assert seen == {"url": "https://or.example.com/api/v1/chat/completions", "auth": "Bearer or-key",
                    "model": "nvidia/nemotron-3.5-super-vl:free"}


def test_nvidia_models_use_the_nvidia_key_and_endpoint(monkeypatch):
    monkeypatch.setattr(settings(), "nvidia_api_key", "nv-key")
    monkeypatch.setattr(settings(), "nvidia_base_url", "https://nv.example.com/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    (nv,) = _build("nvidia:nvidia/nemotron-3.5-lightning-30b-a3b")
    nv.transport = httpx.MockTransport(handler)
    collect(nv, [{"role": "user", "content": "hi"}])
    assert seen == {"url": "https://nv.example.com/v1/chat/completions", "auth": "Bearer nv-key",
                    "model": "nvidia/nemotron-3.5-lightning-30b-a3b"}
