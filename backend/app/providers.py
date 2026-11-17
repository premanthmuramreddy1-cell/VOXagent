"""Speech-to-text, text-to-speech and LLM providers behind interfaces, chosen by config."""

import asyncio
import json
import logging
import re
import time
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx

from app.config import settings
from app.db import record_error

log = logging.getLogger("vox")
SAMPLE_RATE = 16_000  # clients send 16 kHz mono 16-bit PCM
CLOUD_ATTEMPTS = 3  # free-tier keys throttle often: try a 429/5xx this many times
CLOUD_BACKOFF_S = (1.0, 2.0)  # seconds to wait before the next attempt
CLOUD_MAX_WAIT_S = 10.0  # a Retry-After header must never stall a turn for long


class ProviderError(Exception):
    """Every configured provider for a stage failed."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    # Provider data that must go back with the call next turn (its id; Gemini's thought signature). Not identity.
    meta: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class LLMEvent:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0
    cut_off: bool = False  # the model stopped at its output limit, so the text is incomplete


@dataclass
class Transcript:
    text: str
    language: str
    confident: bool


class LLM(Protocol):
    name: str

    def stream(
        self, messages: list[dict], tools: list[dict] | None = None, schema: dict | None = None
    ) -> AsyncIterator[LLMEvent]: ...


class STT(Protocol):
    name: str

    async def transcribe(self, pcm: bytes, language: str | None = None) -> Transcript: ...


class TTS(Protocol):
    name: str

    async def synthesize(self, text: str, language: str) -> bytes: ...  # returns a WAV file


def text_tool_call(text: str, names: set[str]) -> ToolCall | None:
    """Turn a tool call the model wrote as text ('{"name": "list_add", "parameters": {...}}') into a real one.

    Badly formed JSON still counts when it names an offered tool: the call then has no arguments, the agent asks
    the model for valid ones, and the user never sees the raw text.
    """
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("name") in names:
        args = data.get("parameters", data.get("arguments", {}))
        return ToolCall(str(data["name"]), args if isinstance(args, dict) else {})
    found = re.search(r'"name"\s*:\s*"(\w+)"', text)
    if found and found.group(1) in names:
        return ToolCall(found.group(1), {})
    return None


class OllamaLLM:
    def __init__(self, model: str):
        self.model, self.name = model, f"ollama:{model}"

    async def stream(self, messages, tools=None, schema=None):
        # Ollama's default window (2-4k tokens) silently drops the start of a long chat: the system prompt and
        # a page the user shared. 16k fits a read page plus history on a laptop.
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True,
                                "options": {"num_ctx": 16384}}
        if tools:
            body["tools"] = tools
        if schema:  # structured calls: low temperature and a hard cap so a small model cannot loop
            body["format"] = schema
            body["options"] |= {"temperature": 0.1, "num_predict": 1500}
        names = {t["function"]["name"] for t in tools or []}
        held, holding, tokens = "", None, 0  # small models sometimes write the tool call as text: hold it back
        async with httpx.AsyncClient(base_url=settings().ollama_url, timeout=120) as http:
            async with http.stream("POST", "/api/chat", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise RuntimeError(chunk["error"])
                    message = chunk.get("message", {})
                    calls = [
                        ToolCall(c["function"]["name"], c["function"].get("arguments") or {})
                        for c in message.get("tool_calls") or []
                    ]
                    tokens = chunk.get("prompt_eval_count", 0) + chunk.get("eval_count", 0)
                    text = message.get("content", "")
                    if names and holding is None and text.strip():
                        holding = text.lstrip().startswith("{")
                    if holding:
                        held += text
                        text = ""
                    yield LLMEvent(text, calls, tokens)
        if held:
            call = text_tool_call(held, names)
            try:  # bare JSON that is not a call ('{}') is no answer either: never show it to the user
                held = "" if isinstance(json.loads(held), dict | list) else held
            except ValueError:
                pass
            yield LLMEvent("" if call else held, [call] if call else [], tokens)


def _openai_messages(messages: list[dict], schema: dict | None = None) -> list[dict]:
    """Rewrite the agent's Ollama-shaped history as OpenAI chat messages.

    OpenAI-compatible APIs want tool call ids, `type: function` and string-encoded arguments;
    Ollama's /api/chat wants none of those. The translation stays here so the agent is provider-neutral.
    """
    out: list[dict] = []
    pending: list[str] = []  # tool call ids waiting for their result, in call order
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for index, call in enumerate(message["tool_calls"]):
                ident = call.get("id") or f"call_{len(out)}_{index}"
                pending.append(ident)
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                calls.append({
                    "id": ident,
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                    },
                    **({"extra_content": call["extra_content"]} if call.get("extra_content") else {}),
                })
            out.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            continue
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": pending.pop(0) if pending else "call_0",
                "content": str(message.get("content") or ""),
            })
            continue
        pending = []
        out.append({"role": role, "content": message.get("content") or ""})
    if schema:  # JSON mode only guarantees syntax, so the shape goes in the prompt
        out.insert(1 if out and out[0]["role"] == "system" else 0, {
            "role": "system",
            "content": "Reply with one JSON object matching this JSON Schema exactly. "
                       "No prose, no markdown fences:\n" + json.dumps(schema, separators=(",", ":")),
        })
    return out


def _retry_wait(header: str | None, fallback: float) -> float:
    """Honour a numeric Retry-After, but cap it so a bad header cannot stall the turn."""
    try:
        return min(max(float(header or ""), 0.0), CLOUD_MAX_WAIT_S)
    except ValueError:
        return fallback


NO_TOOLS = {"role": "system", "content": "No tools are available for this reply. Answer in plain text only."}


def _out_of_quota(retry_after: str | None, detail: str) -> bool:
    """A 429 that will not clear within a turn: a long Retry-After, or a daily or monthly quota message."""
    try:
        if float(retry_after or "") > CLOUD_MAX_WAIT_S:
            return True
    except ValueError:
        pass
    text = detail.lower()
    return any(word in text for word in ("per day", "daily", "per month", "quota", "insufficient"))


class CloudLLM:
    """Any OpenAI-compatible chat completions API (Z.ai GLM, Groq, OpenRouter, Gemini, vLLM...).

    Streaming is SSE. Tool calls arrive as deltas that must be accumulated by index.
    A 429 or 5xx before the first token is retried with backoff; retrying mid-stream would
    duplicate text already spoken, so once anything is emitted a failure is final.
    """

    def __init__(self, model: str, transport: httpx.AsyncBaseTransport | None = None, vendor: str = "cloud"):
        # vendor picks the key and base URL: "cloud" uses VOX_LLM_*, "groq" VOX_GROQ_*, "meta" VOX_META_*, so a throttled cloud hands
        # the turn to another one
        self.model, self.name, self.transport, self.vendor = model, f"{vendor}:{model}", transport, vendor
        self.fail_fast = False  # set by _build for every model that has another one after it

    def _endpoint(self) -> tuple[str, str]:
        prefix = "llm" if self.vendor == "cloud" else self.vendor
        cfg = settings()
        return getattr(cfg, f"{prefix}_api_key"), getattr(cfg, f"{prefix}_base_url")

    async def stream(self, messages, tools=None, schema=None, _plain=False, tool_choice="auto"):
        key, base = self._endpoint()
        if not key:
            raise RuntimeError(f"VOX_{'LLM' if self.vendor == 'cloud' else self.vendor.upper()}_API_KEY is not set")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(messages, schema),
            "stream": True,
            "stream_options": {"include_usage": True},  # Gemini only reports usage (and so cost) when asked
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if schema:  # structured calls: low temperature and a hard cap so a small model cannot loop
            body["response_format"] = {"type": "json_object"}
            body["temperature"] = 0.1
            body["max_tokens"] = 1500
        url = f"{base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        for attempt in range(1, CLOUD_ATTEMPTS + 1):
            wait = CLOUD_BACKOFF_S[min(attempt - 1, len(CLOUD_BACKOFF_S) - 1)]
            async with httpx.AsyncClient(timeout=120, transport=self.transport) as http:
                async with http.stream("POST", url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        if response.status_code == 400 and tools and ("tool" in detail.lower() or "validation" in detail.lower()):
                            log.warning("Tool call validation rejected by %s, retrying without tools: %s", self.name, detail)
                            async for evt in self.stream(messages, tools=None, schema=schema):
                                yield evt
                            return
                        # with another model behind this one, a throttled reply hands the turn over now: waiting
                        # out a rate limit stalls a spoken answer for seconds
                        retryable = (response.status_code == 429 or response.status_code >= 500) and not self.fail_fast
                        if response.status_code == 429 and _out_of_quota(response.headers.get("retry-after"), detail):
                            retryable = False  # minutes or a day away: the next provider (a local model) answers now
                        if not retryable or attempt == CLOUD_ATTEMPTS:
                            raise RuntimeError(f"{response.status_code} from {self.name}: {detail}")
                        wait = _retry_wait(response.headers.get("retry-after"), wait)
                    else:
                        calls: dict[int, dict[str, str]] = {}
                        tokens, emitted, cut_off = 0, False, False
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            chunk = json.loads(data)
                            if "error" in chunk:
                                error = chunk["error"]
                                if tool_choice == "required" and "did not call a tool" in str(error):
                                    # forced, but the model chose to answer in words: let it (Groq errors instead).
                                    # Words already sent are the answer; with none yet, ask again without forcing.
                                    if not emitted:
                                        async for evt in self.stream(messages, tools, schema, _plain, tool_choice="auto"):
                                            yield evt
                                    return
                                stray = isinstance(error, dict) and error.get("code") == "tool_use_failed"
                                if stray and not tools and not emitted and not _plain:
                                    # No tools were offered, yet the model wrote a tool call. Nothing was spoken
                                    # yet, so ask once more for plain text instead of failing the whole turn.
                                    log.warning("%s called a tool that was not offered, retrying as plain text", self.name)
                                    async for evt in self.stream([*messages, NO_TOOLS], None, schema, _plain=True):
                                        yield evt
                                    return
                                raise RuntimeError(str(error))
                            usage = chunk.get("usage") or {}
                            tokens = max(tokens, int(usage.get("total_tokens") or 0))
                            for choice in chunk.get("choices") or []:
                                cut_off = cut_off or choice.get("finish_reason") == "length"
                                delta = choice.get("delta") or {}
                                for call in delta.get("tool_calls") or []:
                                    index = call.get("index")
                                    if index is None:  # Gemini: no index, each call arrives whole with its own id
                                        index = len(calls) if call.get("id") or not calls else max(calls)
                                    slot = calls.setdefault(int(index), {"name": "", "arguments": ""})
                                    for key in ("id", "extra_content"):  # Gemini 3 needs its signature back
                                        if call.get(key):
                                            slot[key] = call[key]
                                    function = call.get("function") or {}
                                    slot["name"] += function.get("name") or ""
                                    arguments = function.get("arguments")
                                    slot["arguments"] += (
                                        arguments if isinstance(arguments, str) else json.dumps(arguments or {})
                                    )
                                text = delta.get("content") or ""
                                if text:
                                    emitted = True
                                    yield LLMEvent(text, tokens=tokens)
                        parsed = []
                        for index in sorted(calls):
                            raw = calls[index]["arguments"] or "{}"
                            try:
                                arguments = json.loads(raw)
                            except ValueError:  # torn arguments: let the agent ask for valid ones
                                arguments = {}
                            meta = {k: calls[index][k] for k in ("id", "extra_content") if k in calls[index]}
                            parsed.append(ToolCall(calls[index]["name"], arguments, meta))
                        yield LLMEvent(tool_calls=parsed, tokens=tokens, cut_off=cut_off)
                        return
            await asyncio.sleep(wait)


class WhisperSTT:
    def __init__(self, size: str):
        self.size, self.name = size, f"whisper:{size}"
        self._model: Any = None

    def _run(self, pcm: bytes, language: str | None) -> Transcript:
        import numpy as np
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]

        if self._model is None:  # first use downloads the model, then it is cached on disk
            self._model = WhisperModel(self.size, device="cpu", compute_type="int8")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        segments, info = self._model.transcribe(audio, language=language, vad_filter=True)
        parts = list(segments)
        text = " ".join(s.text.strip() for s in parts).strip()
        score = sum(s.avg_logprob for s in parts) / len(parts) if parts else -9.0
        return Transcript(text, info.language, bool(text) and score > -1.0)

    async def transcribe(self, pcm, language=None):
        return await asyncio.to_thread(self._run, pcm, language)


class SayTTS:
    """macOS built-in speech. Covers English, Hindi, Telugu and Tamil with no downloads."""

    name = "say"
    voices = {"en": "Samantha", "hi": "Lekha", "te": "Geeta", "ta": "Vani"}

    async def synthesize(self, text, language):
        voice = self.voices.get(language[:2], self.voices["en"])
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "in.txt", Path(tmp) / "out.wav"
            src.write_text(text)  # via a file so text is never parsed as a flag
            proc = await asyncio.create_subprocess_exec(
                "say", "-v", voice, "-f", str(src), "-o", str(out), "--data-format=LEI16@22050"
            )
            if await proc.wait() != 0:
                raise RuntimeError(f"say exited with {proc.returncode}")
            return out.read_bytes()


_FACTORIES: dict[str, Callable[[str], Any]] = {
    "ollama": OllamaLLM,
    "cloud": CloudLLM,
    "openai": CloudLLM,  # alias: the protocol, not the vendor
    "groq": lambda model: CloudLLM(model, vendor="groq"),
    "meta": lambda model: CloudLLM(model, vendor="meta"),
    "openrouter": lambda model: CloudLLM(model, vendor="openrouter"),
    "nvidia": lambda model: CloudLLM(model, vendor="nvidia"),
    "whisper": WhisperSTT,
    "say": lambda _arg: SayTTS(),
}


def _build(spec: str) -> list[Any]:
    """'ollama:llama3.2,ollama:other' -> providers in fallback order."""
    out = []
    for item in spec.split(","):
        kind, _, arg = item.strip().partition(":")
        out.append(_FACTORIES[kind](arg))
    for provider in out[:-1]:
        if hasattr(provider, "fail_fast"):
            provider.fail_fast = True
    return out


@dataclass
class Providers:
    llm: Sequence[LLM]
    stt: Sequence[STT]
    tts: Sequence[TTS]


@lru_cache
def providers() -> Providers:
    s = settings()
    return Providers(_build(s.llm), _build(s.stt), _build(s.tts))


T = TypeVar("T")
P = TypeVar("P")


async def first_ok(stage: str, candidates: Sequence[P], call: Callable[[P], Awaitable[T]]) -> tuple[T, str]:
    """Run `call` on each provider in order; return the first success and that provider's name."""
    for provider in candidates:
        try:
            return await call(provider), provider.name  # type: ignore[attr-defined]
        except Exception as exc:  # any provider failure moves on to the next provider
            _failed(stage, provider, exc)
    raise ProviderError(f"all {stage} providers failed")


# Live routing: what each model did lately. A model that just failed rests (a rate limit until it resets, a dead key
# for longer) so every turn does not wait on it again; the ones that work are tried fastest first.
HEALTH: dict[str, dict[str, float]] = {}  # name -> {"until": monotonic time it rests until, "speed": seconds}
RETRY_IN_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)


def _rest_for(exc: Exception) -> float:
    text = str(exc)
    if isinstance(exc, TimeoutError):
        return 60
    if text.startswith(("401", "403", "404")):  # a key or model that is not allowed will not fix itself soon
        return 600
    if text.startswith("429"):
        wait = RETRY_IN_RE.search(text)
        return min(300, int(wait.group(1) or 0) * 60 + float(wait.group(2))) if wait else 30
    return 20


def ranked(candidates: Sequence[LLM]) -> list[LLM]:
    """Rested models first, fastest measured first (config order breaks ties and ranks the unmeasured); the last
    model (the local one) stays last."""
    if len(candidates) < 2:
        return list(candidates)
    *pool, last = candidates
    now, order = time.monotonic(), {id(p): i for i, p in enumerate(pool)}
    seen = {p.name: HEALTH.get(p.name, {}) for p in pool}
    tired = sorted((p for p in pool if seen[p.name].get("until", 0) > now), key=lambda p: seen[p.name]["until"])
    ready = sorted((p for p in pool if p not in tired), key=lambda p: (seen[p.name].get("speed", 1e9), order[id(p)]))
    return [*ready, *tired, last]


def _measured(provider: Any, seconds: float) -> None:
    entry = HEALTH.setdefault(provider.name, {})
    entry["speed"] = seconds if "speed" not in entry else 0.7 * entry["speed"] + 0.3 * seconds
    entry["until"] = 0


async def llm_stream(
    candidates: Sequence[LLM], messages: list[dict], tools: list[dict] | None = None, schema: dict | None = None,
    force_tool: bool = False,
) -> AsyncIterator[LLMEvent]:
    """Stream from the best LLM that works right now (see ranked). Falls back only if nothing was emitted yet.

    A provider that is slow to start (cloud models have taken 8-46 s under load) gets llm_first_token_s before the
    next one takes over; the last provider has no deadline, so a local model can still load cold.
    """
    candidates = ranked(candidates)
    for provider in candidates:
        started = False
        # force_tool: cloud models must call one of the tools; others cannot be forced and keep "auto"
        extra = {"tool_choice": "required"} if force_tool and tools and isinstance(provider, CloudLLM) else {}
        stream = provider.stream(messages, tools, schema, **extra).__aiter__()
        began = time.monotonic()
        try:
            try:
                first = (await stream.__anext__() if provider is candidates[-1]
                         else await asyncio.wait_for(stream.__anext__(), settings().llm_first_token_s))
            except StopAsyncIteration:
                return
            _measured(provider, time.monotonic() - began)
            started = True
            yield first
            async for event in stream:
                yield event
            return
        except Exception as exc:
            _failed("llm", provider, TimeoutError(f"no reply within {settings().llm_first_token_s:g} s")
                    if isinstance(exc, TimeoutError) else exc)
            HEALTH.setdefault(provider.name, {})["until"] = time.monotonic() + _rest_for(exc)
            await _close(stream)
            if started:
                break
    raise ProviderError("all llm providers failed")


async def _close(stream: Any) -> None:
    try:
        await stream.aclose()
    except Exception as exc:  # a half-cancelled stream may fail to close; it is abandoned either way
        log.debug("abandoned llm stream did not close cleanly: %s", exc)


def _failed(stage: str, provider: Any, exc: Exception) -> None:
    log.warning("provider failed", extra={"fields": {"stage": stage, "provider": provider.name, "error": str(exc)}})
    record_error(stage, f"{provider.name}: {exc}")
