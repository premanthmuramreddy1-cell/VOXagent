"""Sessions, uploads, reviews, skills, usage and admin endpoints."""

import math
import secrets
import statistics
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import providers as prov
from app.agent import Agent
from app.config import settings
from app.db import (Activity, BotLink, ChatSession, ErrorEvent, Message, Review, Upload, UsageEvent, User, get_db, now)
from app.security import admin_user, check_daily_limit, current_user, sha256
from app.skills import RunContext, discover
from app.skills.code_review import Args, skill, to_markdown

router = APIRouter()


def _owned(db: Session, model: type, ident: str, user: User):
    row = db.get(model, ident)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "not found")
    return row


@router.post("/sessions")
def create_session(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    chat = ChatSession(user_id=user.id)
    db.add(chat)
    db.commit()
    return {"id": chat.id, "title": chat.title}


@router.get("/sessions")
def list_sessions(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    query = select(ChatSession).where(ChatSession.user_id == user.id)
    if term := " ".join(q.split())[:100]:
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        said = select(Message.id).where(Message.session_id == ChatSession.id, Message.text.ilike(like, escape="\\"))
        query = query.where(ChatSession.title.ilike(like, escape="\\") | said.exists())
    rows = db.scalars(query.order_by(ChatSession.created_at.desc()))
    return [{"id": c.id, "title": c.title, "created_at": c.created_at.isoformat()} for c in rows]


class SessionPatch(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: SessionPatch, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    chat = _owned(db, ChatSession, session_id, user)
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(422, "Give the session a name.")
    chat.title = title
    db.commit()
    return {"id": chat.id, "title": chat.title}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    from app.api import ws  # late import: ws imports the agent stack

    chat = _owned(db, ChatSession, session_id, user)
    if channel := ws._channels.pop(session_id, None):
        channel.cancel_turn()
    for link in db.scalars(select(BotLink).where(BotLink.session_id == session_id)):
        fresh = ChatSession(user_id=user.id, title=f"{link.platform.title()} chat")  # the bot keeps working
        db.add(fresh)
        db.flush()
        link.session_id = fresh.id
    db.execute(delete(Message).where(Message.session_id == session_id))
    db.execute(delete(Activity).where(Activity.session_id == session_id))
    db.delete(chat)
    db.commit()
    return Response(status_code=204)


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    _owned(db, ChatSession, session_id, user)
    rows = db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))
    return [{"role": m.role, "text": m.text, "plan": m.plan} for m in rows]


@router.get("/sessions/{session_id}/export")
def export_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    chat = _owned(db, ChatSession, session_id, user)
    rows = db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))
    parts = [f"# {chat.title}", f"_Exported from VoxAgent, {now():%Y-%m-%d %H:%M} UTC_"]
    for m in rows:
        if m.text:
            parts.append(f"**{'You' if m.role == 'user' else 'VoxAgent'}:**\n\n{m.text}")
    headers = {"Content-Disposition": f'attachment; filename="chat-{chat.id[:8]}.md"'}
    return Response("\n\n".join(parts) + "\n", media_type="text/markdown", headers=headers)


@router.get("/sessions/{session_id}/activity")
def list_activity(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    _owned(db, ChatSession, session_id, user)
    rows = db.scalars(select(Activity).where(Activity.session_id == session_id).order_by(Activity.ts))
    return [
        {"action": a.action, "reason": a.reason, "permission": a.permission, "outcome": a.outcome,
         "ts": a.ts.isoformat()}
        for a in rows
    ]


@router.post("/uploads")
async def upload(file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.skills.see import MEDIA  # here: skills import the API's models

    media = Path(file.filename or "").suffix.lower() in MEDIA
    limit = settings().max_media_bytes if media else settings().max_upload_bytes
    blob = await file.read(limit + 1)
    if len(blob) > limit:
        raise HTTPException(413, f"file is larger than {limit // 1_000_000} MB")
    name = Path(file.filename or "upload").name  # drop any directory part the client sent
    if name in ("", ".", ".."):  # these name the folder itself, not a file in it
        name = "upload"
    row = Upload(user_id=user.id, filename=name, size=len(blob))
    db.add(row)
    db.commit()
    folder = Path(settings().data_dir) / "uploads" / user.id / row.id
    folder.mkdir(parents=True)
    (folder / name).write_bytes(blob)
    return {"id": row.id, "filename": name, "size": len(blob)}


def _review_json(review: Review) -> dict:
    return {
        "id": review.id, "source": review.source, "score": review.score, "breakdown": review.breakdown,
        "issues": review.issues, "not_run": review.not_run, "created_at": review.created_at.isoformat(),
    }


@router.post("/reviews")
async def create_review(args: Args, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    check_daily_limit(db, user, "review", settings().free_daily_reviews)  # 429, not the skill's 400 for bad input
    agent = Agent(prov.providers().code or prov.providers().llm, {})  # a review is code work

    async def quiet(*_args, **_kwargs) -> None: ...

    ctx = RunContext(user.id, "", args.code, [], quiet, lambda m, s=None: agent.complete(user.id, m, s))
    try:
        result = await skill.run(args, ctx)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return _review_json(db.get(Review, result.data["review_id"]))  # type: ignore[arg-type]


@router.get("/reviews")
def list_reviews(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Review).where(Review.user_id == user.id).order_by(Review.created_at.desc()))
    return [{"id": r.id, "source": r.source, "score": r.score, "issue_count": len(r.issues),
             "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/reviews/{review_id}")
def get_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _review_json(_owned(db, Review, review_id, user))


@router.get("/reviews/{review_id}/export")
def export_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    review = _owned(db, Review, review_id, user)
    headers = {"Content-Disposition": f'attachment; filename="review-{review.id[:8]}.md"'}
    return Response(to_markdown(review), media_type="text/markdown", headers=headers)


@router.delete("/reviews/{review_id}", status_code=204)
def delete_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    db.delete(_owned(db, Review, review_id, user))  # its share link stops working; the day's usage still counts
    db.commit()
    return Response(status_code=204)


@router.post("/reviews/{review_id}/share")
def share_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    review = _owned(db, Review, review_id, user)
    token = secrets.token_urlsafe(24)
    review.share_token_hash = sha256(token)  # a new link replaces the old one
    db.commit()
    return {"token": token}


@router.get("/shared/{token}")
def shared_review(token: str, db: Session = Depends(get_db)) -> dict:
    review = db.scalar(select(Review).where(Review.share_token_hash == sha256(token)))
    if not review:
        raise HTTPException(404, "this link is not valid")
    return _review_json(review)


@router.get("/skills")
def list_skills(_user: User = Depends(current_user)) -> list[dict]:
    return [{"name": s.name, "description": s.description, "permission": s.permission.value}
            for s in discover().values()]


def _usage(db: Session, *where) -> dict[str, dict]:
    rows = db.execute(
        select(UsageEvent.kind, func.count(), func.sum(UsageEvent.units), func.sum(UsageEvent.cost_usd))
        .where(*where).group_by(UsageEvent.kind)
    )
    return {kind: {"count": n, "units": round(units or 0, 1), "cost_usd": cost or 0} for kind, n, units, cost in rows}


@router.get("/usage/me")
def my_usage(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    today = _usage(db, UsageEvent.user_id == user.id, UsageEvent.ts > now() - timedelta(days=1))
    limits = {"message": settings().free_daily_messages, "review": settings().free_daily_reviews}
    return {"tier": user.tier, "last_24h": today, "daily_limits": limits if user.tier == "free" else None}


@router.get("/admin/overview")
def admin_overview(_admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    usage = _usage(db)
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(200))
    errors = db.scalars(select(ErrorEvent).order_by(ErrorEvent.ts.desc()).limit(50))
    return {
        "users": [{"email": u.email, "tier": u.tier, "created_at": u.created_at.isoformat()} for u in users],
        "usage": usage,
        "skills": sorted(
            ({"skill": k.removeprefix("skill:"), "runs": v["count"]} for k, v in usage.items() if k.startswith("skill:")),
            key=lambda row: -row["runs"],
        ),
        "errors": [{"stage": e.stage, "message": e.message, "ts": e.ts.isoformat()} for e in errors],
    }


@router.get("/metrics")
def metrics(_admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    """Latency per pipeline stage over the last 24 hours, in milliseconds."""
    rows = db.execute(
        select(UsageEvent.kind, UsageEvent.latency_ms)
        .where(UsageEvent.ts > now() - timedelta(days=1), UsageEvent.latency_ms > 0)
    )
    by_kind: dict[str, list[int]] = {}
    for kind, ms in rows:
        by_kind.setdefault(kind, []).append(ms)
    return {
        kind: {"count": len(v), "p50": round(statistics.median(v)), "p95": sorted(v)[math.ceil(len(v) * 0.95) - 1]}
        for kind, v in by_kind.items()
    }

