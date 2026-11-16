"""Speech-to-text, text-to-speech and LLM providers behind interfaces, chosen by config."""

import asyncio
import json
import logging
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


@dataclass
class LLMEvent:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0


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


class OllamaLLM:
    def __init__(self, model: str):
        self.model, self.name = model, f"ollama:{model}"

    async def stream(self, messages, tools=None, schema=None):
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if schema:  # structured calls: low temperature and a hard cap so a small model cannot loop
            body["format"] = schema
            body["options"] = {"temperature": 0.1, "num_predict": 1500}
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
                    yield LLMEvent(message.get("content", ""), calls, tokens)


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


class CloudLLM:
    """Any OpenAI-compatible chat completions API (Z.ai GLM, Groq, OpenRouter, Gemini, vLLM...).

    Streaming is SSE. Tool calls arrive as deltas that must be accumulated by index.
    A 429 or 5xx before the first token is retried with backoff; retrying mid-stream would
    duplicate text already spoken, so once anything is emitted a failure is final.
    """

    def __init__(self, model: str, transport: httpx.AsyncBaseTransport | None = None):
        self.model, self.name, self.transport = model, f"cloud:{model}", transport

    async def stream(self, messages, tools=None, schema=None, _plain=False):
        cfg = settings()
        if not cfg.llm_api_key:
            raise RuntimeError("VOX_LLM_API_KEY is not set")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(messages, schema),
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if schema:  # structured calls: low temperature and a hard cap so a small model cannot loop
            body["response_format"] = {"type": "json_object"}
            body["temperature"] = 0.1
            body["max_tokens"] = 1500
        url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.llm_api_key}"}
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
                        retryable = response.status_code == 429 or response.status_code >= 500
                        if not retryable or attempt == CLOUD_ATTEMPTS:
                            raise RuntimeError(f"{response.status_code} from {self.name}: {detail}")
                        wait = _retry_wait(response.headers.get("retry-after"), wait)
                    else:
                        calls: dict[int, dict[str, str]] = {}
                        tokens, emitted = 0, False
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            chunk = json.loads(data)
                            if "error" in chunk:
                                error = chunk["error"]
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
                                delta = choice.get("delta") or {}
                                for call in delta.get("tool_calls") or []:
                                    slot = calls.setdefault(
                                        int(call.get("index") or 0), {"name": "", "arguments": ""}
                                    )
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
                            parsed.append(ToolCall(calls[index]["name"], arguments))
                        yield LLMEvent(tool_calls=parsed, tokens=tokens)
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
    "whisper": WhisperSTT,
    "say": lambda _arg: SayTTS(),
}


def _build(spec: str) -> list[Any]:
    """'ollama:llama3.2,ollama:other' -> providers in fallback order."""
    out = []
    for item in spec.split(","):
        kind, _, arg = item.strip().partition(":")
        out.append(_FACTORIES[kind](arg))
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


async def llm_stream(
    candidates: Sequence[LLM], messages: list[dict], tools: list[dict] | None = None, schema: dict | None = None
) -> AsyncIterator[LLMEvent]:
    """Stream from the first LLM that works. Falls back only if nothing was emitted yet."""
    for provider in candidates:
        started = False
        try:
            async for event in provider.stream(messages, tools, schema):
                started = True
                yield event
            return
        except Exception as exc:
            _failed("llm", provider, exc)
            if started:
                break
    raise ProviderError("all llm providers failed")


def _failed(stage: str, provider: Any, exc: Exception) -> None:
    log.warning("provider failed", extra={"fields": {"stage": stage, "provider": provider.name, "error": str(exc)}})
    record_error(stage, f"{provider.name}: {exc}")
