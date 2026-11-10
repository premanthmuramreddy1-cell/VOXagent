import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Activity, Base, ChatSession, Message, RefreshToken, Review, Upload, UsageEvent, User, get_db, now
from app.security import current_user, hash_password, make_access_token, rate_limit, sha256, verify_password

router = APIRouter()
COOKIE = "vox_refresh"


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


@router.post("/auth/signup", dependencies=[Depends(rate_limit)])
def signup(body: Credentials, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "email already registered")
    user = User(
        email=email, password_hash=hash_password(body.password), name=body.name,
        is_admin=email in settings().admin_emails,
    )
    db.add(user)
    db.commit()
    return _issue(user, db, response)


@router.post("/auth/login", dependencies=[Depends(rate_limit)])
def login(body: Credentials, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "wrong email or password")
    return _issue(user, db, response)


@router.post("/auth/refresh")
def refresh(response: Response, vox_refresh: str = Cookie(""), db: Session = Depends(get_db)) -> dict:
    row = db.get(RefreshToken, sha256(vox_refresh))
    if not row or row.expires_at < now():
        raise HTTPException(401, "session expired, log in again")
    user = db.get(User, row.user_id)
    db.delete(row)  # rotate: each refresh token works once
    if not user:
        raise HTTPException(401, "unknown user")
    return _issue(user, db, response)


@router.post("/auth/logout")
def logout(response: Response, vox_refresh: str = Cookie(""), db: Session = Depends(get_db)) -> dict:
    db.execute(delete(RefreshToken).where(RefreshToken.token_hash == sha256(vox_refresh)))
    db.commit()
    response.delete_cookie(COOKIE, path="/auth")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return _profile(user)


@router.patch("/me")
def update_me(body: ProfilePatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    for key, value in body.model_dump(exclude_none=True).items():
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
    }


@router.delete("/me")
def delete_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    session_ids = select(ChatSession.id).where(ChatSession.user_id == user.id)
    db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
    for model in (ChatSession, RefreshToken, Activity, Upload, Review, UsageEvent):
        db.execute(delete(model).where(model.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    shutil.rmtree(Path(settings().data_dir) / "uploads" / user.id, ignore_errors=True)
    return {"ok": True}
