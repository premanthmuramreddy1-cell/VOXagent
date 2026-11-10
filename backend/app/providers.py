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
        if schema:
            body["format"] = schema
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


class WhisperSTT:
    def __init__(self, size: str):
        self.size, self.name = size, f"whisper:{size}"
        self._model: Any = None

    def _run(self, pcm: bytes, language: str | None) -> Transcript:
        import numpy as np
        from faster_whisper import WhisperModel

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
