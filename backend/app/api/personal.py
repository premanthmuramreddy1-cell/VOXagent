"""Memory, notes, reminders, settings, push subscriptions and the encrypted form-filling profile."""

import re
from datetime import date, timedelta
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import life, personal, push
from app.crypto import load_secret, save_secret
from app.db import Habit, ListItem, Payment, Memory, Note, PushSubscription, Reminder, User, get_db, now
from app.security import current_user
from app.skills.life_tools import CLOCK

router = APIRouter()

SENSITIVE_KEY = re.compile(r"pass(word)?|pwd|card|cvv|cvc|iban|ssn|aadhaar|pan|routing|account.?number|pin\b|otp",
                           re.IGNORECASE)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def looks_sensitive(key: str, value: str) -> bool:
    """Passwords and card numbers are never stored, whatever the field is called."""
    digits = re.sub(r"[\s-]", "", value)
    return bool(SENSITIVE_KEY.search(key)) or (digits.isdigit() and 13 <= len(digits) <= 19 and _luhn(digits))


def _own(db: Session, model: type, ident: str, user: User):
    row = db.get(model, ident)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "not found")
    return row


# ---------------------------------------------------------------- settings

class SettingsBody(BaseModel):
    tz: str | None = None
    briefing: bool | None = None


@router.get("/me/settings")
def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = personal.user_settings(db, user.id)
    return {"tz": row.tz, "briefing": row.briefing}


@router.put("/me/settings")
def put_settings(body: SettingsBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = personal.user_settings(db, user.id)
    if body.tz is not None:
        try:
            ZoneInfo(body.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "unknown time zone") from None
        row.tz = body.tz
    if body.briefing is not None:
        row.briefing = body.briefing
    db.commit()
    return {"tz": row.tz, "briefing": row.briefing}


# ---------------------------------------------------------------- memory (view, edit, delete)

class MemoryBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="fact", pattern="^(preference|fact|style)$")


def _memory(m: Memory) -> dict:
    return {"id": m.id, "kind": m.kind, "text": m.text, "updated_at": m.updated_at.isoformat()}


@router.get("/memory")
def list_memory(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_memory(m) for m in db.scalars(select(Memory).where(Memory.user_id == user.id)
                                            .order_by(Memory.updated_at.desc()))]


@router.post("/memory")
def add_memory(body: MemoryBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = Memory(user_id=user.id, text=body.text.strip(), kind=body.kind)
    db.add(row)
    db.commit()
    return _memory(row)


@router.put("/memory/{memory_id}")
def edit_memory(memory_id: str, body: MemoryBody, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    row = _own(db, Memory, memory_id, user)
    row.text, row.kind, row.updated_at = body.text.strip(), body.kind, now()
    db.commit()
    return _memory(row)


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, Memory, memory_id, user))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- notes

class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


def _note(n: Note) -> dict:
    return {"id": n.id, "text": n.text, "tags": n.tags, "created_at": n.created_at.isoformat()}


@router.get("/notes")
def list_notes(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    if q.strip():
        return [_note(n) for n in personal.search_notes(db, user.id, q, 50)]
    return [_note(n) for n in db.scalars(select(Note).where(Note.user_id == user.id)
                                          .order_by(Note.created_at.desc()).limit(100))]


@router.post("/notes")
def add_note(body: NoteBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not body.text.strip():
        raise HTTPException(422, "A note needs some text.")
    row = Note(user_id=user.id, text=body.text.strip(), tags=personal.auto_tags(body.text))
    db.add(row)
    db.commit()
    return _note(row)


@router.put("/notes/{note_id}")
def edit_note(note_id: str, body: NoteBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _own(db, Note, note_id, user)
    if not body.text.strip():
        raise HTTPException(422, "A note needs some text.")
    row.text, row.tags = body.text.strip(), personal.auto_tags(body.text)
    db.commit()
    return _note(row)


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, Note, note_id, user))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- lists (the same ones the assistant fills by voice)

class ListItemBody(BaseModel):
    list: str = Field(default="", max_length=60)
    text: str = Field(min_length=1, max_length=120)


@router.get("/lists")
def get_lists(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, list[dict]]:
    lists: dict[str, list[dict]] = {}
    for row in db.scalars(select(ListItem).where(ListItem.user_id == user.id).order_by(ListItem.created_at)):
        lists.setdefault(row.list, []).append({"id": row.id, "text": row.text})
    return lists


@router.post("/lists/items")
def add_list_item(body: ListItemBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    name, text = life.list_name(body.list), body.text.strip()
    if not text:
        raise HTTPException(422, "Write what to add.")
    same = next((i for i in life.items_on(db, user.id, name) if i.text.lower() == text.lower()), None)
    row = same or ListItem(user_id=user.id, list=name, text=text)  # like the voice tool: no duplicates
    if not same:
        db.add(row)
        db.commit()
    return {"id": row.id, "list": name, "text": row.text}


@router.delete("/lists/items/{item_id}")
def remove_list_item(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, ListItem, item_id, user))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- habits (the same ones the assistant tracks by voice)

class HabitBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    target: int = Field(default=1, ge=1, le=100)
    unit: str = Field(default="time", max_length=20)
    times: list[str] = Field(default_factory=list, max_length=12)  # local HH:MM, nudged when one passes unlogged


class HabitLog(BaseModel):
    count: int = Field(ge=-20, le=100)


def _habit(h: Habit, db: Session) -> dict:
    today = personal.local_now(db, h.user_id).date()
    return {"id": h.id, "name": h.name, "target": h.target, "unit": h.unit, "times": h.times or [],
            "done": life.done_on(h, today), "streak": life.streak(h, today)}


@router.get("/habits")
def list_habits(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_habit(h, db) for h in db.scalars(select(Habit).where(Habit.user_id == user.id).order_by(Habit.name))]


@router.post("/habits")
def add_habit(body: HabitBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Give the habit a name.")
    row = life.find_habit(db, user.id, name)  # same name as one already tracked: update it, like the voice tool
    if row is None or row.name.lower() != name.lower():
        row = Habit(user_id=user.id, name=name)
        db.add(row)
    times = sorted({t.strip().zfill(5) for t in body.times if t.strip()})
    if bad := [t for t in times if not CLOCK.match(t)]:
        raise HTTPException(422, f"Give times like 09:00 or 21:30, not {', '.join(bad)}.")
    row.target, row.unit, row.times = max(body.target, len(times)), body.unit.strip() or "time", times
    db.commit()
    return _habit(row, db)


@router.post("/habits/{habit_id}/log")
def log_habit(habit_id: str, body: HabitLog, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _own(db, Habit, habit_id, user)
    life.log_habit(row, personal.local_now(db, user.id).date(), body.count)
    db.commit()
    return _habit(row, db)


@router.delete("/habits/{habit_id}")
def remove_habit(habit_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, Habit, habit_id, user))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- bills and subscriptions (tracked only, never paid)

class PaymentBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["subscription", "bill"] = "bill"
    amount: float = Field(default=0, ge=0, le=10_000_000)
    cycle: Literal["week", "month", "year"] = "month"
    next_due: date


def _payment(p: Payment, today: date) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "amount": p.amount, "currency": p.currency, "cycle": p.cycle,
            "next_due": p.next_due, "autopay": p.autopay, "due_in": (date.fromisoformat(p.next_due) - today).days}


@router.get("/payments")
def list_payments(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    today = personal.local_now(db, user.id).date()
    rows = list(db.scalars(select(Payment).where(Payment.user_id == user.id).order_by(Payment.next_due)))
    return {"items": [_payment(p, today) for p in rows],
            "monthly": {c: round(v, 2) for c, v in life.monthly_totals(rows).items()}}


@router.post("/payments")
def track_payment(body: PaymentBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Give the payment a name.")
    row = life.find_payment(db, user.id, name)
    if row is None or row.name.lower() != name.lower():  # same name: update it, like the voice tool
        row = Payment(user_id=user.id, name=name, currency=life.local_currency(personal.zone(db, user.id).key))
        db.add(row)
    row.kind, row.amount, row.cycle = body.kind, body.amount, body.cycle
    row.next_due, row.day = body.next_due.isoformat(), body.next_due.day
    db.commit()
    return _payment(row, personal.local_now(db, user.id).date())


@router.post("/payments/{payment_id}/paid")
def pay(payment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _own(db, Payment, payment_id, user)
    today = personal.local_now(db, user.id).date()
    life.mark_paid(row, today)  # moves it to its next due date
    db.commit()
    return _payment(row, today)


@router.delete("/payments/{payment_id}")
def stop_payment(payment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, Payment, payment_id, user))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- reminders

class ReminderBody(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    when: str = Field(description="Local ISO date-time, e.g. 2026-11-16T09:00")
    repeat: str = Field(default="", pattern="^(|daily|weekdays|weekly)$")


def _reminder(r: Reminder, db: Session) -> dict:
    tz = personal.zone(db, r.user_id)
    return {"id": r.id, "text": r.text, "due": personal.to_local(r.due_at, tz).isoformat(), "repeat": r.repeat,
            "done": r.done}


@router.get("/reminders")
def list_reminders(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Reminder).where(Reminder.user_id == user.id, Reminder.done.is_(False))
                      .order_by(Reminder.due_at))
    return [_reminder(r, db) for r in rows]


@router.post("/reminders")
def add_reminder(body: ReminderBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        due = personal.to_utc(body.when, personal.zone(db, user.id))
    except ValueError:
        raise HTTPException(422, "use a date and time like 2026-11-16T09:00") from None
    row = Reminder(user_id=user.id, text=body.text.strip(), due_at=due, repeat=body.repeat)
    db.add(row)
    db.commit()
    return _reminder(row, db)


@router.put("/reminders/{reminder_id}")
def edit_reminder(reminder_id: str, body: ReminderBody, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    row = _own(db, Reminder, reminder_id, user)
    try:
        row.due_at = personal.to_utc(body.when, personal.zone(db, user.id))
    except ValueError:
        raise HTTPException(422, "use a date and time like 2026-11-16T09:00") from None
    row.text, row.repeat = body.text.strip(), body.repeat
    db.commit()
    return _reminder(row, db)


@router.post("/reminders/{reminder_id}/done")
def finish_reminder(reminder_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _own(db, Reminder, reminder_id, user)
    personal.complete(row, personal.zone(db, user.id))
    db.commit()
    return {"ok": True, "reminder": None if row.done else _reminder(row, db)}


class SnoozeBody(BaseModel):
    minutes: int = Field(ge=5, le=24 * 60)


@router.post("/reminders/{reminder_id}/snooze")
def snooze_reminder(reminder_id: str, body: SnoozeBody, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    row = _own(db, Reminder, reminder_id, user)
    if row.repeat:
        raise HTTPException(422, "Repeating reminders keep their time; mark this one done instead.")
    row.due_at, row.done = now() + timedelta(minutes=body.minutes), False
    db.commit()
    return _reminder(row, db)


@router.delete("/reminders/{reminder_id}")
def delete_reminder(reminder_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, Reminder, reminder_id, user))
    db.commit()
    return {"ok": True}


@router.get("/reminders/briefing")
def get_briefing(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"text": personal.briefing(db, user.id)}


# ---------------------------------------------------------------- push

PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")


class PushBody(BaseModel):
    endpoint: str = Field(pattern=r"^https://")
    keys: dict[str, str]


@router.get("/push/key")
def push_key() -> dict:
    return {"key": push.public_key()}


@router.post("/push/subscribe")
def subscribe(body: PushBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not {"p256dh", "auth"} <= body.keys.keys():
        raise HTTPException(422, "subscription keys missing")
    host = urlparse(body.endpoint).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):  # else the server posts wherever it is told
        raise HTTPException(422, "not a browser push service")
    try:
        valid = len(push.unb64(body.keys["p256dh"])) == 65 and len(push.unb64(body.keys["auth"])) == 16
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "subscription keys are malformed")
    existing = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if existing:
        db.delete(existing)
    db.add(PushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys["p256dh"],
                            auth=body.keys["auth"]))
    db.commit()
    return {"ok": True}


@router.post("/push/test")
async def push_test(user: User = Depends(current_user)) -> dict:
    return {"sent": await push.notify(user.id, "VoxAgent", "Notifications are working.", "/#/me")}


# ---------------------------------------------------------------- form-filling profile (encrypted)

@router.get("/me/profile")
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return load_secret(db, user.id, "profile") or {}


@router.put("/me/profile")
def put_profile(body: dict[str, str], user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if len(body) > 60 or any(len(k) > 60 or len(v) > 500 for k, v in body.items()):
        raise HTTPException(413, "profile is too large")
    refused = [k for k, v in body.items() if looks_sensitive(k, v)]
    if refused:
        raise HTTPException(422, f"passwords and card numbers are never stored: {', '.join(refused)}")
    clean = {k.strip(): v.strip() for k, v in body.items() if k.strip() and v.strip()}
    save_secret(db, user.id, "profile", clean)
    return clean
