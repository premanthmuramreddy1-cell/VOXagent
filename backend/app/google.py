"""Google account connection (OAuth 2 authorization code flow) for Gmail sending and Calendar events.

Tokens are stored encrypted (app.crypto). The access token refreshes itself from the refresh token.
"""

import base64
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import load_secret, save_secret

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPES = ("openid email https://www.googleapis.com/auth/gmail.send "
          "https://www.googleapis.com/auth/calendar.events")
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in a MockTransport


class GoogleError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings().google_client_id and settings().google_client_secret)


def redirect_uri() -> str:
    return f"{settings().public_url}/integrations/google/callback"


def state_token(user_id: str, kind: str) -> str:
    """Signed, short-lived OAuth state that ties the callback to the user who started it (CSRF guard)."""
    payload = {"sub": user_id, "kind": kind, "exp": datetime.now(UTC) + timedelta(minutes=10)}
    return jwt.encode(payload, settings().secret_key, algorithm="HS256")


def read_state(state: str, kind: str) -> str:
    try:
        claims = jwt.decode(state, settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise GoogleError("the connection link expired, start again") from None
    if claims.get("kind") != kind:
        raise GoogleError("wrong connection state")
    return str(claims["sub"])


def connect_url(user_id: str) -> str:
    if not configured():
        raise GoogleError("Google is not configured on this server (VOX_GOOGLE_CLIENT_ID / _SECRET)")
    return AUTH + "?" + urlencode({
        "client_id": settings().google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state_token(user_id, "google"),
    })


async def _token_request(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as http:
        response = await http.post(TOKEN, data={**data, "client_id": settings().google_client_id,
                                                "client_secret": settings().google_client_secret})
    if response.status_code != 200:
        raise GoogleError(f"Google refused the token request: {response.text[:200]}")
    return response.json()


async def finish_connect(db: Session, code: str, state: str) -> str:
    user_id = read_state(state, "google")
    tokens = await _token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri()})
    if not tokens.get("refresh_token"):
        raise GoogleError("Google did not return a refresh token; remove VoxAgent from your Google account and retry")
    save_secret(db, user_id, "google", {"refresh_token": tokens["refresh_token"], "access_token": tokens["access_token"],
                                        "expires_at": time.time() + int(tokens.get("expires_in", 3600))})
    return user_id


async def access_token(db: Session, user_id: str) -> str:
    stored = load_secret(db, user_id, "google")
    if not stored:
        raise GoogleError("connect your Google account first (Account page)")
    if stored["expires_at"] - 60 > time.time():
        return stored["access_token"]
    fresh = await _token_request({"refresh_token": stored["refresh_token"], "grant_type": "refresh_token"})
    stored.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)))
    save_secret(db, user_id, "google", stored)
    return stored["access_token"]


async def _api(db: Session, user_id: str, method: str, url: str, **kw) -> dict:
    token = await access_token(db, user_id)
    async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as http:
        response = await http.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    if response.status_code >= 300:
        raise GoogleError(f"Google API error {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else {}


async def send_email(db: Session, user_id: str, to: list[str], subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"], message["Subject"] = ", ".join(to), subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = await _api(db, user_id, "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                      json={"raw": raw})
    return str(sent.get("id", ""))


CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


async def create_event(db: Session, user_id: str, title: str, start: str, end: str, tz: str,
                       description: str = "", attendees: list[str] | None = None) -> dict:
    body = {"summary": title, "description": description, "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz}, "attendees": [{"email": a} for a in attendees or []]}
    event = await _api(db, user_id, "POST", CAL, json=body)
    return {"id": event.get("id"), "link": event.get("htmlLink")}


async def list_events(db: Session, user_id: str, start: datetime, end: datetime) -> list[dict]:
    data = await _api(db, user_id, "GET", CAL, params={
        "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
        "orderBy": "startTime", "maxResults": "20"})
    return [{"title": e.get("summary", "(no title)"),
             "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")}
            for e in data.get("items", [])]
