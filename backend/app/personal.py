"""Personal context: the user's time zone, memory, project notes, voice notes, reminders and the daily briefing."""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import workspace as wsp
from app.db import Memory, Note, Reminder, UserSetting, Workspace, now

STOP = set("""a an and are as at be but by for from has have i in is it its me my of on or our so that the this to was
we were will with you your just about into then than them they what when where which who why how can could would
should there here also very really""".split())


def user_settings(db: Session, user_id: str) -> UserSetting:
    row = db.get(UserSetting, user_id)
    if row is None:
        row = UserSetting(user_id=user_id)
        db.add(row)
        db.commit()
    return row


def zone(db: Session, user_id: str) -> ZoneInfo:
    try:
        return ZoneInfo(user_settings(db, user_id).tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_now(db: Session, user_id: str) -> datetime:
    return datetime.now(zone(db, user_id))


def to_utc(local_iso: str, tz: ZoneInfo) -> datetime:
    """Parse '2026-11-16T09:00' (local) or an ISO string with an offset into naive UTC for storage."""
    when = datetime.fromisoformat(local_iso.strip())
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return when.astimezone(UTC).replace(tzinfo=None)


def to_local(utc_naive: datetime, tz: ZoneInfo) -> datetime:
    return utc_naive.replace(tzinfo=UTC).astimezone(tz)


# ---------------------------------------------------------------- what the agent knows at session start

def context_for(db: Session, user_id: str) -> str:
    """Memory, current project notes and the local time, given to the agent at the start of every turn."""
    tz = zone(db, user_id)
    parts = [f"Current local time: {datetime.now(tz):%A %Y-%m-%d %H:%M} ({tz.key})."]
    memories = list(db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.updated_at.desc())
                               .limit(40)))
    if memories:
        parts.append("What you remember about the user:\n" + "\n".join(f"- ({m.kind}) {m.text}" for m in memories))
    space = db.scalar(select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.opened_at.desc()))
    if space:
        notes = wsp.root(space) / wsp.NOTES
        if notes.is_file():
            parts.append(f"Current project: {space.name}. Project notes:\n{notes.read_text(errors='replace')[:2500]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- notes

def auto_tags(text: str, limit: int = 4) -> list[str]:
    """Cheap, deterministic tags: hashtags first, then the most frequent meaningful words."""
    tags = [t.lower() for t in re.findall(r"#(\w+)", text)]
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text) if w.lower() not in STOP]
    for word, _ in Counter(words).most_common():
        if len(tags) >= limit:
            break
        if word not in tags:
            tags.append(word)
    return tags[:limit]


def search_notes(db: Session, user_id: str, query: str, limit: int = 10) -> list[Note]:
    terms = [t for t in re.findall(r"\w+", query.lower()) if t not in STOP] or [query.lower()]
    rows = db.scalars(select(Note).where(Note.user_id == user_id,
                                         or_(*[Note.text.ilike(f"%{t}%") for t in terms]))
                      .order_by(Note.created_at.desc()).limit(200))
    scored = []
    for note in rows:
        hay = (note.text + " " + " ".join(note.tags)).lower()
        scored.append((sum(hay.count(t) for t in terms) + 2 * sum(t in note.tags for t in terms), note))
    return [n for s, n in sorted(scored, key=lambda pair: -pair[0]) if s][:limit]


# ---------------------------------------------------------------- reminders

def next_due(reminder: Reminder, tz: ZoneInfo) -> datetime | None:
    """Next occurrence for a recurring reminder, computed in the user's zone so 9 a.m. stays 9 a.m. across DST."""
    if not reminder.repeat:
        return None
    local = to_local(reminder.due_at, tz)
    step = {"daily": 1, "weekdays": 1, "weekly": 7}[reminder.repeat]
    local += timedelta(days=step)
    while reminder.repeat == "weekdays" and local.weekday() >= 5:
        local += timedelta(days=1)
    return local.astimezone(UTC).replace(tzinfo=None)


def due_reminders(db: Session) -> list[Reminder]:
    return list(db.scalars(select(Reminder).where(Reminder.done.is_(False), Reminder.due_at <= now())))


def fire(db: Session, reminder: Reminder) -> None:
    """Mark a reminder delivered: one-off ones finish, recurring ones roll forward."""
    following = next_due(reminder, zone(db, reminder.user_id))
    if following:
        reminder.due_at = following
    else:
        reminder.done = True
    db.commit()


def briefing(db: Session, user_id: str, events: list[dict] | None = None) -> str:
    tz = zone(db, user_id)
    today = datetime.now(tz)
    start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    rows = db.scalars(select(Reminder).where(
        Reminder.user_id == user_id, Reminder.done.is_(False),
        Reminder.due_at >= start.astimezone(UTC).replace(tzinfo=None),
        Reminder.due_at < end.astimezone(UTC).replace(tzinfo=None)).order_by(Reminder.due_at))
    items = [f"{to_local(r.due_at, tz):%H:%M} {r.text}" for r in rows]
    greeting = "morning" if today.hour < 12 else "afternoon" if today.hour < 17 else "evening"
    lines = [f"Good {greeting}. It is {today:%A, %d %B}."]
    lines.append(f"You have {len(items)} reminder(s) today: " + "; ".join(items) + "." if items
                 else "No reminders today.")
    if events is not None:
        named = [f"{e['start'][11:16] or 'all day'} {e['title']}" for e in events]
        lines.append(f"{len(named)} calendar event(s): " + "; ".join(named) + "." if named else "No calendar events today.")
    return " ".join(lines)
