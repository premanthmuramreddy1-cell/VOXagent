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


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    name: Mapped[str]
    opened_at: Mapped[datetime] = mapped_column(default=now, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class PendingChange(Base):
    """A staged file change waiting for the user to accept or reject it."""
    __tablename__ = "pending_changes"
    id: Mapped[str] = _pk()
    workspace_id: Mapped[str] = _fk("workspaces.id")
    kind: Mapped[str]  # write | delete | rename
    path: Mapped[str]
    new_path: Mapped[str] = mapped_column(default="")
    content: Mapped[str | None] = mapped_column(Text, default=None)
    diff: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    kind: Mapped[str] = mapped_column(default="fact")  # preference | fact | style
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    text: Mapped[str]
    due_at: Mapped[datetime] = mapped_column(index=True)
    repeat: Mapped[str] = mapped_column(default="")  # "" | daily | weekdays | weekly
    done: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Payment(Base):
    """A subscription or bill the user pays on a cycle. VoxAgent only tracks and nudges; it never pays."""
    __tablename__ = "payments"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    kind: Mapped[str] = mapped_column(default="subscription")  # subscription | bill
    name: Mapped[str]
    amount: Mapped[float] = mapped_column(default=0)
    currency: Mapped[str] = mapped_column(default="")
    cycle: Mapped[str] = mapped_column(default="month")  # week | month | year
    next_due: Mapped[str] = mapped_column(default="")  # local date, YYYY-MM-DD
    day: Mapped[int] = mapped_column(default=0)  # day of month it falls on, so Jan 31 -> Feb 28 -> Mar 31
    remind_days: Mapped[int] = mapped_column(default=2)
    autopay: Mapped[bool] = mapped_column(default=False)
    last_paid: Mapped[str] = mapped_column(default="")
    nudged: Mapped[str] = mapped_column(default="")  # "<due>:<days-before>" of the last nudge, so each fires once


class Habit(Base):
    """A daily habit: a target count ('8 glasses of water') and/or fixed times ('vitamin at 09:00 and 21:00')."""
    __tablename__ = "habits"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    name: Mapped[str]
    target: Mapped[int] = mapped_column(default=1)
    unit: Mapped[str] = mapped_column(default="time")
    times: Mapped[list[Any]] = mapped_column(default=list)  # ["09:00", "21:00"] local
    log: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"YYYY-MM-DD": count}
    nudged: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"<day>:<slot>": true}


class ListItem(Base):
    __tablename__ = "list_items"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    list: Mapped[str] = mapped_column(default="groceries", index=True)
    text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=now)


class Secret(Base):
    """Encrypted per-user credentials (OAuth tokens, GitHub token, form profile)."""
    __tablename__ = "secrets"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    kind: Mapped[str] = mapped_column(index=True)  # github | google | profile | ...
    blob: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str]
    auth: Mapped[str]


class BotLink(Base):
    """Links a Telegram chat or WhatsApp number to a VoxAgent account."""
    __tablename__ = "bot_links"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users.id")
    platform: Mapped[str]  # telegram | whatsapp
    external_id: Mapped[str] = mapped_column(index=True)
    session_id: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class UserSetting(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tz: Mapped[str] = mapped_column(default="UTC")  # IANA zone the browser reports
    briefing: Mapped[bool] = mapped_column(default=True)  # daily spoken briefing push
    last_briefing: Mapped[str] = mapped_column(default="")  # local date of the last one sent


class LinkCode(Base):
    __tablename__ = "link_codes"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[str] = _fk("users.id")
    expires_at: Mapped[datetime]


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
