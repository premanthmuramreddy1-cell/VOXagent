"""Voice and chat over one WebSocket.

Client -> server (JSON): auth {token} first, then text.input {text, upload_ids?, speak?},
audio.start, audio.stop {speak?}, barge_in, cancel, confirm {id, decision}, resume {last_seq}.
Binary frames are 16 kHz mono 16-bit PCM while recording.
Server -> client (JSON, each with seq): ready, transcript.partial, transcript.final, agent.restate,
plan.created, plan.step, tool.started, tool.finished, answer.delta, answer.done, clarify,
confirm.request, tts.stop, turn.started, turn.done, error {code, retryable, message}. Binary frames are WAV speech.
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app import brain, personal
from app import providers as prov
from app.agent import Agent, Turn
from app.config import settings
from app.db import ChatSession, Message, SessionLocal, User, new_id, record, record_error
from app.security import check_daily_limit, user_from_token
from app.skills import discover
from app.spoken import normalize

router = APIRouter()
log = logging.getLogger("vox")
SENTENCE = re.compile(r"\s*(.+?[.!?।:\n])(?=\s)", re.S)
LINK = re.compile(r"\[([^\]\n]+)\]\(https?://[^)\s]+\)")
LINE_MARK = re.compile(r"^\s*(#{1,6}|[-*•])\s+", re.M)
INLINE_MARK = re.compile(r"\*\*|`|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])")


def speakable(text: str) -> str:
    """Drop Markdown symbols the screen draws but a voice would read out ("asterisk", "hash")."""
    return INLINE_MARK.sub("", LINE_MARK.sub("", LINK.sub(r"\1", text)))
BYTES_PER_S = prov.SAMPLE_RATE * 2
LEARNING: set[asyncio.Task] = set()  # preference learning still running after its turn ended
SORRY = "Sorry, I didn't catch that. Could you say it again?"
CHANNEL_TTL_S = 3600
MAX_CHANNELS = 200


class Channel:
    """Per-session state that outlives a dropped socket, so a client can reconnect and resume."""

    def __init__(self) -> None:
        self.events: deque[dict] = deque(maxlen=500)
        self.seq = 0
        self.ws: WebSocket | None = None
        self.turn: asyncio.Task | None = None
        self.partial: asyncio.Task | None = None
        self.pending: tuple[str, asyncio.Future[str]] | None = None
        self.audio = bytearray()
        self.recording = False
        self.partial_at = 0
        self.updated_at = time.monotonic()

    def touch(self) -> None:
        self.updated_at = time.monotonic()

    async def emit(self, type: str, **payload: Any) -> None:
        self.touch()
        self.seq += 1
        event = {"type": type, "seq": self.seq, **payload}
        self.events.append(event)
        await self.send(event)

    async def send(self, data: dict | bytes) -> None:
        if self.ws is None:
            return  # client is away; JSON events wait in the buffer for resume
        try:
            await (self.ws.send_bytes(data) if isinstance(data, bytes) else self.ws.send_json(data))
        except (WebSocketDisconnect, RuntimeError):
            self.ws = None

    async def confirm(self, request: dict) -> str:
        ident, future = new_id(), asyncio.get_running_loop().create_future()
        self.pending = (ident, future)
        await self.emit("confirm.request", id=ident, **request)
        try:
            return await asyncio.wait_for(future, 120)
        except TimeoutError:
            return "no"  # silence is never consent
        finally:
            self.pending = None

    def cancel_turn(self) -> bool:
        running = self.turn is not None and not self.turn.done()
        if running and self.turn:
            self.turn.cancel()
        return running


# shortcut: channels live in process memory, move to Redis streams when running more than one worker.
_channels: dict[str, Channel] = {}


def _prune_channels() -> None:
    """Evict stale disconnected channels so process memory stays bounded."""
    now = time.monotonic()
    stale = [
        sid for sid, ch in _channels.items()
        if ch.ws is None and (ch.turn is None or ch.turn.done()) and (now - ch.updated_at > CHANNEL_TTL_S)
    ]
    for sid in stale:
        _channels.pop(sid, None)
    if len(_channels) > MAX_CHANNELS:
        inactive = sorted(
            (sid for sid, ch in _channels.items() if ch.ws is None and (ch.turn is None or ch.turn.done())),
            key=lambda sid: _channels[sid].updated_at,
        )
        for sid in inactive[: len(_channels) - MAX_CHANNELS]:
            _channels.pop(sid, None)


class Speaker:
    """Wraps emit: turns streamed answer text into speech sentence by sentence."""

    def __init__(self, channel: Channel, user_id: str, enabled: bool, started: float):
        self.channel, self.user_id, self.enabled, self.started = channel, user_id, enabled, started
        self.language, self.buffer, self.first, self.in_code = "en", "", True, False
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.worker = asyncio.create_task(self._work()) if enabled else None

    async def emit(self, type: str, **payload: Any) -> None:
        await self.channel.emit(type, **payload)
        if not self.enabled:
            return
        self.language = payload.get("language", self.language)
        if type == "clarify":
            self.queue.put_nowait(payload["question"])
        if type == "answer.delta":
            self.buffer += payload["text"]
            while match := SENTENCE.match(self.buffer):
                self._say(match.group(1))
                self.buffer = self.buffer[match.end():]

    def _say(self, text: str) -> None:
        """Queue text for speech, skipping fenced code blocks: code is shown, not read aloud."""
        if not self.enabled:
            return
        for part in text.split("```"):
            if not self.in_code and (said := speakable(part).strip()):
                self.queue.put_nowait(said)
            self.in_code = not self.in_code
        self.in_code = not self.in_code  # split yields one more part than there are fences

    async def finish(self) -> None:
        if self.worker:
            if self.enabled:
                self._say(self.buffer)
                self.queue.put_nowait(None)
            await self.worker

    def cancel(self) -> None:
        if self.worker and not self.worker.done():
            self.worker.cancel()

    async def _work(self) -> None:
        while (text := await self.queue.get()) is not None:
            start = time.perf_counter()
            try:
                wav, name = await prov.first_ok(
                    "tts", prov.providers().tts, lambda p, t=text: p.synthesize(t, self.language)  # type: ignore[misc]
                )
            except prov.ProviderError:  # the written answer is still shown; disable further queueing
                self.enabled = False
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await self.channel.emit("error", code="tts_failed", retryable=False, message="Speech is unavailable.")
                return
            record("tts", self.user_id, name, len(text), round((time.perf_counter() - start) * 1000))
            if self.first:
                self.first = False
                record("first_audio", self.user_id, latency_ms=round((time.perf_counter() - self.started) * 1000))
            await self.channel.send(wav)


async def _transcribe(channel: Channel, user: User, pcm: bytes) -> prov.Transcript:
    start = time.perf_counter()
    transcript, name = await prov.first_ok("stt", prov.providers().stt, lambda p: p.transcribe(pcm))
    record("stt", user.id, name, len(pcm) / BYTES_PER_S, round((time.perf_counter() - start) * 1000))
    transcript.text = normalize(transcript.text)
    await channel.emit("transcript.final", text=transcript.text, language=transcript.language)
    return transcript


def _save_user_message(user: User, session_id: str, text: str) -> list[dict]:
    """Check the daily limit, store the message, return recent history for the model."""
    with SessionLocal() as db:
        check_daily_limit(db, user, "message", settings().free_daily_messages)
        recent = db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc()).limit(10)
        ).all()
        chat = db.get(ChatSession, session_id)
        if chat and chat.title == "New session":
            chat.title = text[:60]
        db.add(Message(session_id=session_id, role="user", text=text))
        db.commit()
    record("message", user.id)
    return [{"role": m.role, "content": m.text} for m in reversed(recent)]


async def _turn(channel: Channel, user: User, session_id: str, text: str, pcm: bytes | None,
                upload_ids: list[str], speaker: Speaker) -> None:
    language = user.preferred_language
    if pcm is not None:
        transcript = await _transcribe(channel, user, pcm) if pcm else None
        if transcript is None or not transcript.confident:  # ask instead of guessing
            await speaker.emit("clarify", question=SORRY)
            return await speaker.finish()
        text, language = transcript.text, transcript.language
    text = text.strip()[: settings().max_text_chars]
    if not text:
        return
    history = _save_user_message(user, session_id, text)
    agent = Agent(prov.providers().llm, discover(), prov.providers().code)
    with SessionLocal() as db:
        context = personal.context_for(db, user.id)
    turn = Turn(user.id, session_id, text, speaker.emit, channel.confirm, upload_ids, history, language, user.name,
                context)
    answer, plan = await agent.run(turn)
    with SessionLocal() as db:
        db.add(Message(session_id=session_id, role="assistant", text=answer, language=plan["language"], plan=plan))
        db.commit()
    learning = asyncio.create_task(brain.learn(  # after the reply, so it can use the strongest (code) models
        lambda m, schema=None: agent.complete(user.id, m, schema, agent.code_llms), user.id, text))
    LEARNING.add(learning)  # held until done: a bare task can be garbage-collected mid-run
    learning.add_done_callback(LEARNING.discard)
    await channel.emit("answer.done", text=answer)
    await speaker.finish()


async def run_turn(channel: Channel, user: User, session_id: str, text: str, pcm: bytes | None,
                   upload_ids: list[str], speak: bool) -> None:
    with SessionLocal() as db:  # a socket lives for hours: tier and language changes must apply to the next turn
        user = db.get(User, user.id) or user
    speaker = Speaker(channel, user.id, speak, time.perf_counter())
    await channel.emit("turn.started")
    try:
        await _turn(channel, user, session_id, text, pcm, upload_ids, speaker)
    except asyncio.CancelledError:
        await channel.emit("tts.stop")
        raise
    except HTTPException as exc:
        await channel.emit("error", code="limit", retryable=False, message=str(exc.detail))
    except prov.ProviderError as exc:
        await channel.emit("error", code="provider_failed", retryable=True, message=str(exc))
    except Exception as exc:  # report every failure to the user instead of hanging the turn
        log.exception("turn failed")
        record_error("turn", repr(exc), user.id)
        await channel.emit("error", code="internal", retryable=True, message="Something went wrong. Please try again.")
    finally:
        speaker.cancel()
        await channel.emit("turn.done")


async def _partial(channel: Channel) -> None:
    try:
        stt = prov.providers().stt[0]
        transcript = await stt.transcribe(bytes(channel.audio[: len(channel.audio) // 2 * 2]))
        if channel.recording and transcript.text:
            await channel.emit("transcript.partial", text=transcript.text)
    except Exception as exc:  # partials are best effort; the final transcript reports real failures
        log.warning("partial transcript failed: %s", exc)


async def _on_audio(channel: Channel, chunk: bytes) -> None:
    if not channel.recording:
        return
    channel.touch()
    channel.audio += chunk
    if len(channel.audio) > settings().max_audio_s * BYTES_PER_S:
        channel.recording = False
        channel.audio.clear()
        await channel.emit("error", code="audio_too_long", retryable=False,
                           message=f"Recordings are limited to {settings().max_audio_s} seconds.")
    # shortcut: partials re-transcribe the whole buffer, switch to a streaming STT when one is configured.
    elif len(channel.audio) - channel.partial_at >= BYTES_PER_S * 1.5 and (
        channel.partial is None or channel.partial.done()
    ):
        channel.partial_at = len(channel.audio)
        channel.partial = asyncio.create_task(_partial(channel))


async def _dispatch(channel: Channel, user: User, session_id: str, msg: dict) -> None:
    channel.touch()
    kind = msg.get("type")
    if kind == "resume":
        for event in [e for e in channel.events if e["seq"] > int(msg.get("last_seq", 0))]:
            await channel.send(event)
    elif kind == "audio.start":
        channel.cancel_turn()  # speaking over the agent is a barge-in
        channel.audio.clear()
        channel.recording, channel.partial_at = True, 0
    elif kind in ("text.input", "audio.stop"):
        old = channel.turn
        if channel.cancel_turn() and old:
            await asyncio.wait([old])  # its tts.stop/turn.done must land before the new turn starts, not inside it
        pcm = None
        if kind == "audio.stop":
            channel.recording, pcm = False, bytes(channel.audio[: len(channel.audio) // 2 * 2])
            channel.audio.clear()
        uploads = [str(u) for u in msg.get("upload_ids", [])][:5]
        channel.turn = asyncio.create_task(
            run_turn(channel, user, session_id, str(msg.get("text", "")), pcm, uploads, bool(msg.get("speak")))
        )
    elif kind in ("barge_in", "cancel"):
        channel.recording = False
        if not channel.cancel_turn():
            await channel.emit("tts.stop")
    elif kind == "confirm":
        if channel.pending and channel.pending[0] == msg.get("id"):
            if not channel.pending[1].done():
                channel.pending[1].set_result(str(msg.get("decision", "no")).lower())
        else:
            await channel.emit(
                "error",
                code="confirm_expired",
                retryable=False,
                message="Confirmation request expired or is no longer active.",
            )
    else:
        await channel.send({"type": "error", "code": "bad_message", "retryable": False, "message": "Unknown message."})


@router.websocket("/ws/sessions/{session_id}")
async def voice(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 10)
        with SessionLocal() as db:
            user = user_from_token(str(hello.get("token", "")), db)
            chat = db.get(ChatSession, session_id)
            if not chat or chat.user_id != user.id:
                raise HTTPException(404)
    # KeyError: the first frame was binary (audio before signing in), which has no text to parse
    except (HTTPException, TimeoutError, ValueError, AttributeError, KeyError, WebSocketDisconnect):
        await ws.close(4401)
        return
    _prune_channels()
    channel = _channels.setdefault(session_id, Channel())
    channel.touch()
    channel.ws = ws
    await ws.send_json({"type": "ready", "seq": channel.seq})
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await _on_audio(channel, message["bytes"])
                continue
            try:
                await _dispatch(channel, user, session_id, json.loads(message.get("text") or ""))
            except (ValueError, TypeError, AttributeError):
                await channel.send({"type": "error", "code": "bad_message", "retryable": False,
                                    "message": "Malformed message."})
    except WebSocketDisconnect:
        pass
    finally:
        channel.touch()
        if channel.ws is ws:
            channel.ws = None
        _prune_channels()
