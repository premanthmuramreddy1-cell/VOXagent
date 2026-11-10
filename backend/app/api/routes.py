"""Sessions, uploads, reviews, skills, usage and admin endpoints."""

import secrets
import statistics
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import providers as prov
from app.agent import Agent
from app.config import settings
from app.db import (Activity, ChatSession, ErrorEvent, Message, Review, Upload, UsageEvent, User, get_db, now,
                    record_error)
from app.security import admin_user, current_user, sha256
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
def list_sessions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.created_at.desc()))
    return [{"id": c.id, "title": c.title, "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    _owned(db, ChatSession, session_id, user)
    rows = db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))
    return [{"role": m.role, "text": m.text, "plan": m.plan} for m in rows]


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
    limit = settings().max_upload_bytes
    blob = await file.read(limit + 1)
    if len(blob) > limit:
        raise HTTPException(413, f"file is larger than {limit // 1_000_000} MB")
    name = Path(file.filename or "upload").name  # drop any directory part the client sent
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
    agent = Agent(prov.providers().llm, {})

    async def quiet(*_args, **_kwargs) -> None: ...

    ctx = RunContext(user.id, "", "", [], quiet, lambda m, s=None: agent.complete(user.id, m, s))
    try:
        result = await skill.run(args, ctx)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except prov.ProviderError as exc:
        record_error("review", str(exc), user.id)
        raise HTTPException(503, "the language model is unavailable, try again shortly") from None
    return _review_json(db.get(Review, result.data["review_id"]))  # type: ignore[arg-type]


@router.get("/reviews")
def list_reviews(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Review).where(Review.user_id == user.id).order_by(Review.created_at.desc()))
    return [{"id": r.id, "source": r.source, "score": r.score, "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/reviews/{review_id}")
def get_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _review_json(_owned(db, Review, review_id, user))


@router.get("/reviews/{review_id}/export")
def export_review(review_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    review = _owned(db, Review, review_id, user)
    headers = {"Content-Disposition": f'attachment; filename="review-{review.id[:8]}.md"'}
    return Response(to_markdown(review), media_type="text/markdown", headers=headers)


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
        kind: {"count": len(v), "p50": round(statistics.median(v)), "p95": sorted(v)[int(len(v) * 0.95) - 1 if len(v) > 1 else 0]}
        for kind, v in by_kind.items()
    }

