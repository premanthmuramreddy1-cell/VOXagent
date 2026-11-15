"""Telegram and WhatsApp clients: thin adapters over the same agent turn the web app uses.

Linking: the web app shows a one-time code; the user sends "/link CODE" (Telegram) or "link CODE" (WhatsApp).
Confirmations become Yes/No buttons (Telegram) or a "yes"/"no" reply (WhatsApp). Voice notes are decoded with
ffmpeg to 16 kHz PCM and go through the same speech-to-text as the web app.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import BotLink, ChatSession, LinkCode, SessionLocal, User, now

log = logging.getLogger("vox.bots")
TRANSPORT: httpx.AsyncBaseTransport | None = None
YES = {"yes", "y", "apply", "haan", "ha", "avunu", "aam", "ok", "okay", "confirm"}
CONFIRM_TIMEOUT_S = 180


def new_link_code(user_id: str) -> str:
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    with SessionLocal() as db:
        db.add(LinkCode(code=code, user_id=user_id, expires_at=now() + timedelta(minutes=10)))
        db.commit()
    return code


def link(platform: str, external_id: str, code: str) -> str | None:
    """Bind a chat to the account that generated `code`. Returns the user's email, or None if invalid."""
    with SessionLocal() as db:
        row = db.get(LinkCode, code.strip().upper())
        if not row or row.expires_at < now():
            return None
        user = db.get(User, row.user_id)
        chat = ChatSession(user_id=row.user_id, title=f"{platform.title()} chat")
        db.add(chat)
        db.flush()
        for old in db.scalars(select(BotLink).where(BotLink.platform == platform, BotLink.external_id == external_id)):
            db.delete(old)
        db.add(BotLink(user_id=row.user_id, platform=platform, external_id=external_id, session_id=chat.id))
        db.delete(row)
        db.commit()
        return user.email if user else None


def linked(platform: str, external_id: str) -> tuple[User, str] | None:
    with SessionLocal() as db:
        row = db.scalar(select(BotLink).where(BotLink.platform == platform, BotLink.external_id == external_id))
        user = db.get(User, row.user_id) if row else None
        return (user, row.session_id) if user and row else None


async def to_pcm(audio: bytes) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(audio), 60)
    return out


class BotChannel:
    """Implements the slice of the WebSocket Channel that run_turn uses: emit, send and confirm."""

    def __init__(self, reply, ask):
        self.reply, self.ask = reply, ask  # async (text) and async (question) callables
        self.answer = ""
        self.waiting: asyncio.Future[str] | None = None

    async def emit(self, type: str, **payload: Any) -> None:
        if type == "answer.delta":
            self.answer += payload.get("text", "")
        elif type in ("answer.done", "clarify"):
            text = payload.get("text") or payload.get("question") or self.answer
            if text.strip():
                await self.reply(text.strip())
            self.answer = ""
        elif type == "server.started":
            await self.reply(f"Preview: {payload.get('url')}")
        elif type == "error":
            await self.reply(f"⚠️ {payload.get('message', 'Something went wrong.')}")

    async def send(self, data: Any) -> None:  # binary speech frames are dropped: replies are text
        return None

    async def confirm(self, request: dict) -> str:
        level = "say yes" if request.get("permission") == "critical" else "say apply"
        await self.ask(f"Allow {request.get('skill')}? ({level}, or no)")
        self.waiting = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self.waiting, CONFIRM_TIMEOUT_S)
        except TimeoutError:
            return "no"
        finally:
            self.waiting = None

    def answer_confirm(self, text: str) -> bool:
        if self.waiting and not self.waiting.done():
            word = text.strip().lower().rstrip(".!")
            self.waiting.set_result("yes" if word in YES and word != "apply" else word)
            return True
        return False


async def _run(channel: BotChannel, user: User, session_id: str, text: str, pcm: bytes | None) -> None:
    from app.api.ws import run_turn  # late import: ws imports the agent stack

    await run_turn(channel, user, session_id, text, pcm, [], False)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Telegram (long polling)

class Telegram:
    def __init__(self, token: str):
        self.api = f"https://api.telegram.org/bot{token}"
        self.files = f"https://api.telegram.org/file/bot{token}"
        self.channels: dict[str, BotChannel] = {}

    async def call(self, method: str, **params: Any) -> Any:
        async with httpx.AsyncClient(timeout=60, transport=TRANSPORT) as http:
            response = await http.post(f"{self.api}/{method}", json=params)
        return response.json().get("result")

    def channel(self, chat_id: str) -> BotChannel:
        if chat_id not in self.channels:
            async def reply(text: str) -> None:
                await self.call("sendMessage", chat_id=chat_id, text=text[:4000])

            async def ask(question: str) -> None:
                await self.call("sendMessage", chat_id=chat_id, text=question, reply_markup={"inline_keyboard": [[
                    {"text": "Yes", "callback_data": "yes"}, {"text": "No", "callback_data": "no"}]]})
            self.channels[chat_id] = BotChannel(reply, ask)
        return self.channels[chat_id]

    async def handle(self, update: dict) -> None:
        if query := update.get("callback_query"):
            chat_id = str(query["message"]["chat"]["id"])
            self.channel(chat_id).answer_confirm(query.get("data", "no"))
            await self.call("answerCallbackQuery", callback_query_id=query["id"])
            return
        message = update.get("message") or {}
        if not message:
            return
        chat_id, text = str(message["chat"]["id"]), (message.get("text") or "").strip()
        channel = self.channel(chat_id)
        if text.startswith("/link"):
            email = link("telegram", chat_id, text.removeprefix("/link").strip())
            await channel.reply(f"Linked to {email}. Talk or type to me." if email else
                                "That code is not valid. Get a fresh one on the VoxAgent Account page.")
            return
        if text.startswith("/start"):
            await channel.reply("Hi! Open VoxAgent → Account → Telegram, then send /link CODE here.")
            return
        account = linked("telegram", chat_id)
        if not account:
            await channel.reply("This chat is not linked yet. Send /link CODE from the VoxAgent Account page.")
            return
        if text and channel.answer_confirm(text):
            return
        pcm = None
        if voice := message.get("voice") or message.get("audio"):
            info = await self.call("getFile", file_id=voice["file_id"])
            async with httpx.AsyncClient(timeout=60, transport=TRANSPORT) as http:
                audio = (await http.get(f"{self.files}/{info['file_path']}")).content
            pcm = await to_pcm(audio)
        if text or pcm:
            asyncio.create_task(_run(channel, account[0], account[1], text, pcm))

    async def poll(self) -> None:
        offset = 0
        while True:
            try:
                updates = await self.call("getUpdates", offset=offset, timeout=50) or []
                for update in updates:
                    offset = update["update_id"] + 1
                    await self.handle(update)
            except asyncio.CancelledError:
                raise
            except Exception:  # keep polling through network blips
                log.exception("telegram poll failed")
                await asyncio.sleep(5)


# ---------------------------------------------------------------- WhatsApp Cloud API (webhook)

GRAPH = "https://graph.facebook.com/v20.0"
_wa_channels: dict[str, BotChannel] = {}


def whatsapp_signature_ok(payload: bytes, header: str) -> bool:
    secret = settings().whatsapp_app_secret
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


async def _wa_send(to: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=30, transport=TRANSPORT) as http:
        await http.post(f"{GRAPH}/{settings().whatsapp_phone_id}/messages",
                        headers={"Authorization": f"Bearer {settings().whatsapp_token}"},
                        json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}})


def _wa_channel(number: str) -> BotChannel:
    if number not in _wa_channels:
        async def reply(text: str) -> None:
            await _wa_send(number, text)
        _wa_channels[number] = BotChannel(reply, reply)
    return _wa_channels[number]


async def whatsapp_message(message: dict) -> None:
    number, kind = str(message.get("from", "")), message.get("type")
    channel = _wa_channel(number)
    text = (message.get("text") or {}).get("body", "").strip() if kind == "text" else ""
    if text.lower().startswith("link "):
        email = link("whatsapp", number, text[5:])
        await channel.reply(f"Linked to {email}." if email else "That code is not valid.")
        return
    account = linked("whatsapp", number)
    if not account:
        await channel.reply("Send 'link CODE' with the code from the VoxAgent Account page.")
        return
    if text and channel.answer_confirm(text):
        return
    pcm = None
    if kind in ("audio", "voice"):
        media_id = (message.get(kind) or {}).get("id")
        headers = {"Authorization": f"Bearer {settings().whatsapp_token}"}
        async with httpx.AsyncClient(timeout=60, transport=TRANSPORT) as http:
            meta = (await http.get(f"{GRAPH}/{media_id}", headers=headers)).json()
            audio = (await http.get(meta["url"], headers=headers)).content
        pcm = await to_pcm(audio)
    if text or pcm:
        asyncio.create_task(_run(channel, account[0], account[1], text, pcm))


@contextlib.asynccontextmanager
async def telegram_running():
    task = asyncio.create_task(Telegram(settings().telegram_token).poll()) if settings().telegram_token else None
    try:
        yield
    finally:
        if task:
            task.cancel()
