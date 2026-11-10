from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def _pk() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def _fk(target: str) -> Mapped[str]:
    return mapped_column(ForeignKey(target, ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str]
    name: Mapped[str] = mapped_column(default="")
    preferred_language: Mapped[str] = mapped_column(default="")
    tier: Mapped[str] = mapped_column(default="free")  # "paid" flag ready for a payment provider
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _fk("users.id")
    expires_at: Mapped[datetime]


class ChatSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    title: Mapped[str] = mapped_column(default="New session")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = _pk()
    session_id: Mapped[str] = _fk("sessions.id")
    role: Mapped[str]  # user | assistant
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(default="")
    plan: Mapped[dict[str, Any] | None] = mapped_column(default=None)  # restatement + steps
    created_at: Mapped[datetime] = mapped_column(default=now)


class Activity(Base):
    __tablename__ = "activity_log"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    session_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    reason: Mapped[str] = mapped_column(Text)
    permission: Mapped[str] = mapped_column(default="read")
    outcome: Mapped[str] = mapped_column(default="ok")
    ts: Mapped[datetime] = mapped_column(default=now)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    filename: Mapped[str]
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=now)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    source: Mapped[str]
    score: Mapped[int]
    breakdown: Mapped[dict[str, Any]]
    issues: Mapped[list[Any]]
    not_run: Mapped[list[Any]]
    share_token_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    kind: Mapped[str]  # llm | stt | tts | skill:<name> | message
    provider: Mapped[str] = mapped_column(default="")
    units: Mapped[float] = mapped_column(default=0)  # tokens, audio seconds or characters
    cost_usd: Mapped[float] = mapped_column(default=0)  # local providers cost nothing
    latency_ms: Mapped[int] = mapped_column(default=0)
    ts: Mapped[datetime] = mapped_column(default=now, index=True)


class ErrorEvent(Base):
    __tablename__ = "error_events"
    id: Mapped[str] = _pk()
    user_id: Mapped[str | None] = mapped_column(default=None)
    stage: Mapped[str]
    message: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(default=now, index=True)


def _engine():
    url = settings().database_url
    if url.startswith("sqlite"):
        kw: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kw["poolclass"] = StaticPool
        return create_engine(url, **kw)
    return create_engine(url)


engine = _engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)
# shortcut: create_all instead of migrations, add Alembic when moving to Postgres.
Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db


def record(kind: str, user_id: str, provider: str = "", units: float = 0, latency_ms: int = 0) -> None:
    with SessionLocal() as db:
        db.add(UsageEvent(user_id=user_id, kind=kind, provider=provider, units=units, latency_ms=latency_ms))
        db.commit()


def record_error(stage: str, message: str, user_id: str | None = None) -> None:
    with SessionLocal() as db:
        db.add(ErrorEvent(stage=stage, message=message[:2000], user_id=user_id))
        db.commit()
