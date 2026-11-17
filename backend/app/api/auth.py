import secrets
import shutil
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app import sandbox
from app.crypto import load_secret
from app.db import (Activity, Base, BotLink, ChatSession, ErrorEvent, Habit, LinkCode, ListItem, Memory, Message, Note,
                    Payment, PendingChange,
                    PushSubscription, RefreshToken, Reminder, Review, Secret, Upload, UsageEvent, User,
                    UserSetting, Workspace, get_db, now)
from app.security import current_user, hash_password, make_access_token, rate_limit, sha256, verify_password

router = APIRouter()
COOKIE = "vox_refresh"
SUPPORTED_LANGUAGES = {"", "en", "hi", "te", "ta"}
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=100)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    preferred_language: str | None = Field(default=None, max_length=20)


def _profile(user: User) -> dict:
    keys = ("id", "email", "name", "preferred_language", "tier", "is_admin")
    return {k: getattr(user, k) for k in keys}


def _issue(user: User, db: Session, response: Response) -> dict:
    token = secrets.token_urlsafe(32)
    expires = now() + timedelta(seconds=settings().refresh_ttl_s)
    db.add(RefreshToken(token_hash=sha256(token), user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=settings().refresh_ttl_s, httponly=True,
        samesite="lax", secure=settings().cookie_secure, path="/auth",
    )
    return {"access_token": make_access_token(user.id), "user": _profile(user)}


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded")


def _local(request: Request) -> bool:
    """On this Mac itself. A tunnel or proxy also connects from 127.0.0.1, so a forwarded request is never local."""
    host = request.client.host if request.client else ""
    return host in LOCAL_HOSTS and not any(h in request.headers for h in PROXY_HEADERS)


def _may_join(email: str) -> None:
    allowed = {e.lower() for e in settings().allowed_emails}
    if allowed and email not in allowed:
        raise HTTPException(403, "sign-up is closed on this VoxAgent")


@router.post("/auth/signup", dependencies=[Depends(rate_limit)])
def signup(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    local = _local(request)
    # Nobody proves they own the address here: from outside this Mac, a password sign-up is refused once sign-up is
    # locked, even for a listed email (anyone could type it). Listed people sign in with Google, which verifies it.
    # Checked first, so a stranger cannot learn which emails have accounts.
    if settings().allowed_emails and not local:
        raise HTTPException(403, "sign-up is closed on this VoxAgent; sign in with Google or on the host machine")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "email already registered")
    admins = {e.lower() for e in settings().admin_emails}
    # A listed admin email only counts when the sign-up comes from the machine VoxAgent runs on (its owner).
    user = User(
        email=email, password_hash=hash_password(body.password), name=body.name.strip(),
        is_admin=email in admins and local,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "email already registered") from None
    return _issue(user, db, response)


@router.post("/auth/login", dependencies=[Depends(rate_limit)])
def login(body: Credentials, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "wrong email or password")
    return _issue(user, db, response)


class GoogleCredential(BaseModel):
    credential: str = Field(min_length=1, max_length=4096)


async def _google_claims(credential: str, transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    # shortcut: Google's tokeninfo endpoint checks signature and expiry so we need no crypto dependency;
    # switch to local JWKS verification if sign-ins exceed a few per second.
    async with httpx.AsyncClient(transport=transport, timeout=10) as http:
        r = await http.get(GOOGLE_TOKENINFO, params={"id_token": credential})
    return r.json() if r.status_code == 200 else None


@router.get("/auth/config")
def auth_config() -> dict:
    return {"google_client_id": settings().google_client_id}


@router.post("/auth/google", dependencies=[Depends(rate_limit)])
async def google_login(body: GoogleCredential, response: Response, db: Session = Depends(get_db)) -> dict:
    client_id = settings().google_client_id
    if not client_id:
        raise HTTPException(404, "Google sign-in is not configured")
    try:
        claims = await _google_claims(body.credential)
    except httpx.HTTPError:
        raise HTTPException(503, "could not reach Google, try again") from None
    if (
        not claims or claims.get("aud") != client_id or claims.get("iss") not in GOOGLE_ISSUERS
        or str(claims.get("email_verified")).lower() != "true" or not claims.get("email")
    ):
        raise HTTPException(401, "Google sign-in failed")
    email = str(claims["email"]).lower()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        _may_join(email)
        admins = {e.lower() for e in settings().admin_emails}
        user = User(email=email, password_hash="", name=str(claims.get("name", ""))[:100], is_admin=email in admins)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # two first sign-ins raced; use the row that won
            db.rollback()
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                raise HTTPException(409, "try again") from None
    return _issue(user, db, response)


@router.post("/auth/refresh")
def refresh(response: Response, vox_refresh: str = Cookie(""), db: Session = Depends(get_db)) -> dict:
    if not vox_refresh:
        raise HTTPException(401, "session expired, log in again")
    row = db.get(RefreshToken, sha256(vox_refresh))
    if not row or row.expires_at < now():
        raise HTTPException(401, "session expired, log in again")
    user = db.get(User, row.user_id)
    db.delete(row)  # rotate: each refresh token works once
    if not user:
        db.commit()
        raise HTTPException(401, "unknown user")
    return _issue(user, db, response)


@router.post("/auth/logout")
def logout(response: Response, vox_refresh: str = Cookie(""), db: Session = Depends(get_db)) -> dict:
    if vox_refresh:
        db.execute(delete(RefreshToken).where(RefreshToken.token_hash == sha256(vox_refresh)))
        db.commit()
    response.delete_cookie(COOKIE, path="/auth")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return _profile(user)


@router.patch("/me")
def update_me(body: ProfilePatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    updates = body.model_dump(exclude_none=True)
    if "name" in updates:
        updates["name"] = updates["name"].strip()
    if "preferred_language" in updates:
        lang = updates["preferred_language"].strip().lower()[:2]
        if lang not in SUPPORTED_LANGUAGES:
            raise HTTPException(422, "unsupported preferred_language")
        updates["preferred_language"] = lang
    for key, value in updates.items():
        setattr(user, key, value)
    db.merge(user)
    db.commit()
    return _profile(user)


def _rows(db: Session, model: type[Base], *where) -> list[dict]:
    cols = [c.name for c in model.__table__.columns if "hash" not in c.name]
    return [{c: str(getattr(r, c)) for c in cols} for r in db.scalars(select(model).where(*where))]


@router.get("/me/export")
def export_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    session_ids = select(ChatSession.id).where(ChatSession.user_id == user.id)
    return {
        "user": _profile(user),
        "sessions": _rows(db, ChatSession, ChatSession.user_id == user.id),
        "messages": _rows(db, Message, Message.session_id.in_(session_ids)),
        "reviews": _rows(db, Review, Review.user_id == user.id),
        "activity": _rows(db, Activity, Activity.user_id == user.id),
        "usage": _rows(db, UsageEvent, UsageEvent.user_id == user.id),
        "memories": _rows(db, Memory, Memory.user_id == user.id),
        "notes": _rows(db, Note, Note.user_id == user.id),
        "reminders": _rows(db, Reminder, Reminder.user_id == user.id),
        "payments": _rows(db, Payment, Payment.user_id == user.id),
        "habits": _rows(db, Habit, Habit.user_id == user.id),
        "lists": _rows(db, ListItem, ListItem.user_id == user.id),
        "workspaces": _rows(db, Workspace, Workspace.user_id == user.id),
        "form_profile": load_secret(db, user.id, "profile") or {},
        "connected": sorted(s.kind for s in db.scalars(select(Secret).where(Secret.user_id == user.id))),
    }


@router.delete("/me")
async def delete_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.api import ws  # late import: ws imports the agent stack

    space_list = set(db.scalars(select(Workspace.id).where(Workspace.user_id == user.id)))
    for server in [s for s in sandbox.SERVERS.values() if s.workspace_id in space_list]:
        await sandbox.stop_server(server.id)  # a running preview would keep serving the deleted account's code
    for chat_id in db.scalars(select(ChatSession.id).where(ChatSession.user_id == user.id)):
        if channel := ws._channels.pop(chat_id, None):
            channel.cancel_turn()
    session_ids = select(ChatSession.id).where(ChatSession.user_id == user.id)
    db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
    space_ids = select(Workspace.id).where(Workspace.user_id == user.id)
    db.execute(delete(PendingChange).where(PendingChange.workspace_id.in_(space_ids)))
    for model in (ChatSession, RefreshToken, Activity, Upload, Review, UsageEvent, Workspace, Memory, Note, Reminder,
                  Payment, Habit, ListItem, Secret, PushSubscription, BotLink, LinkCode, UserSetting, ErrorEvent):
        db.execute(delete(model).where(model.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    for folder in ("uploads", "workspaces"):
        shutil.rmtree(Path(settings().data_dir) / folder / user.id, ignore_errors=True)
    return {"ok": True}
