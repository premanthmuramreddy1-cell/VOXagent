"""Memory, project notes, voice notes, reminders and the daily briefing."""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import personal
from app import workspace as wsp
from app.db import Memory, Note, Reminder, SessionLocal, now
from app.skills import Permission, RunContext, SkillResult


class RememberArgs(BaseModel):
    text: str = Field(description="The fact to remember, e.g. 'prefers tabs over spaces' or 'name is Asha'.")
    kind: Literal["preference", "fact", "style"] = "fact"


class Remember:
    name, input_schema, permission = "remember", RememberArgs, Permission.READ
    description = "Remember something about the user for future sessions: name, preferences, code style, language."

    async def run(self, args: RememberArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = Memory(user_id=ctx.user_id, kind=args.kind, text=args.text.strip()[:500])
            db.add(row)
            db.commit()
        return SkillResult(f"I'll remember that: {args.text}", {"id": row.id})


class ForgetArgs(BaseModel):
    about: str = Field(description="Words identifying what to forget.")


class Forget:
    name, input_schema, permission = "forget", ForgetArgs, Permission.CHANGE
    description = "Forget remembered facts about the user that match the given words."

    async def run(self, args: ForgetArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            rows = list(db.scalars(select(Memory).where(Memory.user_id == ctx.user_id,
                                                        Memory.text.ilike(f"%{args.about.strip()}%"))))
            for row in rows:
                db.delete(row)
            db.commit()
        return SkillResult(f"Forgot {len(rows)} thing(s)." if rows else "I had nothing remembered about that.",
                           {"forgotten": [r.text for r in rows]})


class Empty(BaseModel):
    pass


class ListMemory:
    name, input_schema, permission = "list_memory", Empty, Permission.READ
    description = "Say what VoxAgent remembers about the user."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            rows = list(db.scalars(select(Memory).where(Memory.user_id == ctx.user_id)))
        return SkillResult("I remember: " + "; ".join(r.text for r in rows) if rows else "I don't remember anything yet.",
                           {"memories": [{"id": r.id, "kind": r.kind, "text": r.text} for r in rows]})


class ProjectNoteArgs(BaseModel):
    section: Literal["Decisions", "Structure", "To-dos"]
    text: str


class UpdateProjectNotes:
    name, input_schema, permission = "update_project_notes", ProjectNoteArgs, Permission.READ
    description = "Record a decision, structure note or to-do in the current project's NOTES.md (read every session)."

    async def run(self, args: ProjectNoteArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            space = await wsp.current(db, ctx.user_id)
        path = wsp.root(space) / wsp.NOTES
        text = path.read_text() if path.exists() else f"# {space.name}\n\n## Decisions\n\n## Structure\n\n## To-dos\n"
        heading = f"## {args.section}\n"
        bullet = f"- {'[ ] ' if args.section == 'To-dos' else ''}{args.text.strip()}\n"
        if heading not in text:
            text += f"\n{heading}"
        start = text.index(heading) + len(heading)
        nxt = text.find("\n## ", start)
        insert = len(text) if nxt == -1 else nxt + 1
        text = text[:insert].rstrip("\n") + "\n" + bullet + ("\n" + text[insert:] if nxt != -1 else "")
        path.write_text(text)
        await wsp.commit(wsp.root(space), f"notes: {args.section.lower()}: {args.text[:60]}")
        return SkillResult(f"Noted under {args.section}.", {"section": args.section})


class NoteArgs(BaseModel):
    text: str = Field(description="What to note down.")


class SaveNote:
    name, input_schema, permission = "save_note", NoteArgs, Permission.READ
    description = "Save a voice note as searchable text with automatic tags."

    async def run(self, args: NoteArgs, ctx: RunContext) -> SkillResult:
        tags = personal.auto_tags(args.text)
        with SessionLocal() as db:
            note = Note(user_id=ctx.user_id, text=args.text.strip(), tags=tags)
            db.add(note)
            db.commit()
        return SkillResult(f"Saved your note, tagged {', '.join(tags) or 'untagged'}.", {"id": note.id, "tags": tags})


class SearchArgs(BaseModel):
    query: str


class SearchNotes:
    name, input_schema, permission = "search_notes", SearchArgs, Permission.READ
    description = "Search the user's saved notes."

    async def run(self, args: SearchArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            notes = personal.search_notes(db, ctx.user_id, args.query)
        if not notes:
            return SkillResult("No notes match that.", {"notes": []})
        return SkillResult("Matching notes: " + " | ".join(n.text[:160] for n in notes[:5]),
                           {"notes": [{"id": n.id, "text": n.text, "tags": n.tags} for n in notes]})


class ReminderArgs(BaseModel):
    text: str = Field(description="What to remind the user about.")
    when: str = Field(description="Local date and time in ISO form, e.g. 2026-11-16T09:00. Use the current local "
                                  "time from the context to resolve words like 'tomorrow'.")
    repeat: Literal["", "daily", "weekdays", "weekly"] = ""


class AddReminder:
    name, input_schema, permission = "add_reminder", ReminderArgs, Permission.READ
    description = "Set a reminder (one-off or recurring daily, weekdays or weekly); it arrives as a push notification."

    async def run(self, args: ReminderArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            tz = personal.zone(db, ctx.user_id)
            due = personal.to_utc(args.when, tz)
            if due < now() - timedelta(minutes=1) and not args.repeat:
                raise ValueError("that time has already passed")
            row = Reminder(user_id=ctx.user_id, text=args.text.strip(), due_at=due, repeat=args.repeat)
            db.add(row)
            db.commit()
            local = personal.to_local(due, tz)
        every = f", repeating {args.repeat}" if args.repeat else ""
        return SkillResult(f"I'll remind you to {args.text} on {local:%A %d %B at %H:%M}{every}.",
                           {"id": row.id, "due": local.isoformat()})


class ListReminders:
    name, input_schema, permission = "list_reminders", Empty, Permission.READ
    description = "List the user's upcoming reminders."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            tz = personal.zone(db, ctx.user_id)
            rows = list(db.scalars(select(Reminder).where(Reminder.user_id == ctx.user_id, Reminder.done.is_(False))
                                   .order_by(Reminder.due_at).limit(20)))
        items = [f"{personal.to_local(r.due_at, tz):%a %d %b %H:%M} {r.text}" for r in rows]
        return SkillResult("Upcoming: " + "; ".join(items) if items else "You have no upcoming reminders.",
                           {"reminders": [{"id": r.id, "text": r.text, "repeat": r.repeat} for r in rows]})


class DoneArgs(BaseModel):
    about: str = Field(description="Words identifying the reminder to cancel or mark done.")
    cancel: bool = Field(default=False, description="True to stop the reminder for good (\"cancel\", \"delete\", "
                         "\"stop reminding me\"); false when the user did it this time (a repeating one comes back).")


class CompleteReminder:
    name, input_schema, permission = "complete_reminder", DoneArgs, Permission.READ
    description = "Mark a reminder done or cancel it."

    async def run(self, args: DoneArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = db.scalar(select(Reminder).where(Reminder.user_id == ctx.user_id, Reminder.done.is_(False),
                                                   Reminder.text.ilike(f"%{args.about.strip()}%")))
            if not row:
                return SkillResult("I couldn't find that reminder.", {})
            if args.cancel:
                row.done = True
            else:
                personal.complete(row, personal.zone(db, ctx.user_id))
            db.commit()
            if args.cancel or row.done:
                return SkillResult(f"{'Cancelled' if args.cancel else 'Done'}: {row.text}.", {"id": row.id})
            when = personal.to_local(row.due_at, personal.zone(db, ctx.user_id))
        return SkillResult(f"Done for now: {row.text}. Next time is {when:%A %-d %B at %H:%M}.", {"id": row.id})


class Briefing:
    name, input_schema, permission = "daily_briefing", Empty, Permission.READ
    description = ("Plan my day: a short spoken briefing of today's reminders, calendar events, bills and renewals "
                   "due soon, habit progress and what is on the user's lists. Relay it as given: do not invent "
                   "times, tasks or a schedule that is not in it.")

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        from app import google

        with SessionLocal() as db:
            events = None
            try:
                start = personal.local_now(db, ctx.user_id).replace(hour=0, minute=0, second=0, microsecond=0)
                events = await google.list_events(db, ctx.user_id, start, start + timedelta(days=1))
            except google.GoogleError:
                events = None  # calendar not connected: brief from reminders alone
            text = personal.briefing(db, ctx.user_id, events)
        return SkillResult(text, {"briefing": text}, final=True)


skills = [Remember(), Forget(), ListMemory(), UpdateProjectNotes(), SaveNote(), SearchNotes(), AddReminder(),
          ListReminders(), CompleteReminder(), Briefing()]
