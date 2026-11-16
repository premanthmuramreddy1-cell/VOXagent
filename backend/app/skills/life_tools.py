"""Subscriptions and bills, daily habits and shopping lists (see app/life.py). Tracking only: nothing here pays."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import life, personal
from app.db import Habit, ListItem, Payment, SessionLocal
from app.skills import Permission, RunContext, SkillResult


def _today(db, user_id: str) -> date:
    return personal.local_now(db, user_id).date()


# ---------------------------------------------------------------- money

class TrackArgs(BaseModel):
    name: str = Field(description="What is paid: 'Netflix', 'electricity bill', 'rent', 'phone EMI'.")
    kind: Literal["subscription", "bill"] = Field(
        description="subscription: a service that renews by itself. bill: something the user pays each time.")
    amount: float = Field(0, ge=0, description="Amount per cycle, 0 if not said.")
    currency: str = Field("", description="Symbol or code the user used (₹, $, EUR); empty if not said.")
    cycle: Literal["week", "month", "year"] = "month"
    next_due: str = Field("", description="Next due or renewal date as YYYY-MM-DD in the user's local time, if known.")
    day_of_month: int = Field(0, ge=0, le=31, description="'due on the 10th' -> 10, when no full date was given.")
    remind_days: int = Field(2, ge=0, le=30, description="Days before the due date to nudge.")
    autopay: bool = Field(False, description="True if it is paid automatically (card or bank mandate).")


class TrackPayment:
    name, input_schema, permission = "track_payment", TrackArgs, Permission.READ
    description = ("Track a subscription or recurring bill ('I pay 649 a month for Netflix, renews on the 5th', "
                   "'add electricity bill of 1500 due on the 10th') and nudge before it falls due. Updates it if "
                   "already tracked. Only tracks; never pays.")

    async def run(self, args: TrackArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            today = _today(db, ctx.user_id)
            due = (date.fromisoformat(args.next_due) if args.next_due
                   else life.first_due(today, args.day_of_month or today.day))
            row = life.find_payment(db, ctx.user_id, args.name) or Payment(user_id=ctx.user_id, name=args.name.strip())
            row.kind, row.cycle, row.remind_days, row.autopay = args.kind, args.cycle, args.remind_days, args.autopay
            row.amount = args.amount or row.amount or 0
            row.currency = args.currency or row.currency or life.local_currency(personal.zone(db, ctx.user_id).key)
            row.next_due, row.day = due.isoformat(), args.day_of_month or due.day
            db.add(row)
            db.commit()
            cost = f"{life.money(row.amount, row.currency)} a {row.cycle}, " if row.amount else ""
            verb = "renews" if row.kind == "subscription" else "due"
            text = (f"Tracking {row.name}: {cost}{verb} {due:%a %d %b}. "
                    f"I'll remind you {row.remind_days} day(s) before.")
        return SkillResult(text, {"id": row.id, "next_due": row.next_due})


class NameArgs(BaseModel):
    name: str


class MarkPaid:
    name, input_schema, permission = "mark_paid", NameArgs, Permission.READ
    description = "Mark a tracked bill or subscription as paid ('I paid the electricity bill'): moves it to its next due date."

    async def run(self, args: NameArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = life.find_payment(db, ctx.user_id, args.name)
            if row is None:
                return SkillResult(f"I'm not tracking anything called {args.name}.", {})
            life.mark_paid(row, _today(db, ctx.user_id))
            db.commit()
            return SkillResult(f"Marked {row.name} paid. Next due {date.fromisoformat(row.next_due):%a %d %b}.",
                               {"next_due": row.next_due})


class ListPaymentsArgs(BaseModel):
    kind: Literal["all", "subscription", "bill"] = "all"


class ListPayments:
    name, input_schema, permission = "list_payments", ListPaymentsArgs, Permission.READ
    description = ("List tracked subscriptions and bills with their next due dates and what they cost per month in "
                   "total ('what are my subscriptions', 'how much do I spend a month', 'what bills are due').")

    async def run(self, args: ListPaymentsArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            query = select(Payment).where(Payment.user_id == ctx.user_id).order_by(Payment.next_due)
            rows = [p for p in db.scalars(query) if args.kind == "all" or p.kind == args.kind]
            if not rows:
                return SkillResult("You're not tracking any subscriptions or bills yet.", {"items": []})
            lines = [f"{p.name}: {life.money(p.amount, p.currency)}/{p.cycle}, next {date.fromisoformat(p.next_due):%d %b}"
                     + (" (autopay)" if p.autopay else "") for p in rows]
            totals = " + ".join(life.money(round(v, 2), c) for c, v in life.monthly_totals(rows).items())
            items = [{"name": p.name, "kind": p.kind, "amount": p.amount, "currency": p.currency, "cycle": p.cycle,
                      "next_due": p.next_due} for p in rows]
        return SkillResult("; ".join(lines) + f". About {totals} a month in total.", {"items": items})


class StopTracking:
    name, input_schema, permission = "stop_tracking_payment", NameArgs, Permission.CHANGE
    description = "Stop tracking a subscription or bill. This does not cancel the service itself."

    async def run(self, args: NameArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = life.find_payment(db, ctx.user_id, args.name)
            if row is None:
                return SkillResult(f"I'm not tracking anything called {args.name}.", {})
            db.delete(row)
            db.commit()
        return SkillResult(f"Stopped tracking {row.name}. To cancel the service itself, cancel it with them.", {})


# ---------------------------------------------------------------- habits

class HabitArgs(BaseModel):
    name: str = Field(description="'Water', 'Vitamin D', 'Workout', 'Read'.")
    target: int = Field(0, ge=0, le=100, description="Times a day; defaults to the number of times given, else 1.")
    unit: str = Field("time", description="'glass', 'dose', 'session', 'page'.")
    times: list[str] = Field(default_factory=list, description="Local reminder times as HH:MM, e.g. ['09:00', '21:00'].")


class AddHabit:
    name, input_schema, permission = "add_habit", HabitArgs, Permission.READ
    description = ("Track a daily habit: a daily target ('drink 8 glasses of water a day') and/or fixed times "
                   "('vitamin at 9 am and 9 pm'). Nudges only when the user falls behind, never at night.")

    async def run(self, args: HabitArgs, ctx: RunContext) -> SkillResult:
        times = sorted({t.strip()[:5].zfill(5) for t in args.times if t.strip()})
        with SessionLocal() as db:
            row = life.find_habit(db, ctx.user_id, args.name) or Habit(user_id=ctx.user_id, name=args.name.strip())
            row.target, row.unit, row.times = args.target or len(times) or 1, args.unit, times
            db.add(row)
            db.commit()
            when = f" at {', '.join(times)}" if times else ""
        return SkillResult(f"Tracking {row.name}: {row.target} {row.unit}(s) a day{when}.", {"id": row.id})


class LogArgs(BaseModel):
    name: str
    count: int = Field(1, ge=-20, le=100, description="How many to add; negative to correct a mistake.")


class LogHabit:
    name, input_schema, permission = "log_habit", LogArgs, Permission.READ
    description = "Log that a habit was done ('I drank 2 glasses of water', 'took my vitamin', 'did my workout')."

    async def run(self, args: LogArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = life.find_habit(db, ctx.user_id, args.name)
            if row is None:
                return SkillResult(f"You're not tracking {args.name} yet. Say 'track {args.name} daily' to start.", {})
            today = _today(db, ctx.user_id)
            done = life.log_habit(row, today, args.count)
            db.commit()
            left = row.target - done
            tail = "Target met for today." if left <= 0 else f"{left} to go today."
            run = life.streak(row, today)
        return SkillResult(f"{row.name}: {done}/{row.target}. {tail}" + (f" {run}-day streak." if run > 1 else ""),
                           {"done": done, "target": row.target, "streak": run})


class Empty(BaseModel):
    pass


class HabitStatus:
    name, input_schema, permission = "habit_status", Empty, Permission.READ
    description = "Today's progress and streaks for every tracked habit ('how am I doing on my habits')."

    async def run(self, args: Empty, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            lines = life.habit_lines(db, ctx.user_id, _today(db, ctx.user_id))
        return SkillResult(lines[0] if lines else "You're not tracking any habits yet.", {})


class RemoveHabit:
    name, input_schema, permission = "remove_habit", NameArgs, Permission.CHANGE
    description = "Stop tracking a habit and drop its history."

    async def run(self, args: NameArgs, ctx: RunContext) -> SkillResult:
        with SessionLocal() as db:
            row = life.find_habit(db, ctx.user_id, args.name)
            if row is None:
                return SkillResult(f"You're not tracking {args.name}.", {})
            db.delete(row)
            db.commit()
        return SkillResult(f"Stopped tracking {row.name}.", {})


# ---------------------------------------------------------------- lists

class ListArgs(BaseModel):
    items: list[str] = Field(description="Separate items: 'milk and eggs' -> ['milk', 'eggs'].")
    list_name: str = Field("groceries", description="Which list: groceries (the default, also 'shopping'), packing, ...")


class AddToList:
    name, input_schema, permission = "list_add", ListArgs, Permission.READ
    description = "Add items to a list ('add milk and eggs to my grocery list', 'we're out of coffee'). Skips duplicates."

    async def run(self, args: ListArgs, ctx: RunContext) -> SkillResult:
        name = life.list_name(args.list_name)
        with SessionLocal() as db:
            have = {i.text.lower() for i in life.items_on(db, ctx.user_id, name)}
            added = []
            for text in (t.strip() for t in args.items):
                if text and text.lower() not in have:
                    db.add(ListItem(user_id=ctx.user_id, list=name, text=text[:120]))
                    have.add(text.lower())
                    added.append(text)
            db.commit()
        said = f"Added {', '.join(added)}" if added else "Those were already there"
        return SkillResult(f"{said}. Your {name} list has {len(have)} item(s).", {"added": added, "list": name})


class RemoveFromList:
    name, input_schema, permission = "list_remove", ListArgs, Permission.READ
    description = "Remove items from a list ('take milk off the grocery list', 'I bought the eggs')."

    async def run(self, args: ListArgs, ctx: RunContext) -> SkillResult:
        wanted = {t.strip().lower() for t in args.items if t.strip()}
        with SessionLocal() as db:
            gone = [i for i in life.items_on(db, ctx.user_id, args.list_name)
                    if i.text.lower() in wanted or any(w in i.text.lower() for w in wanted)]
            for item in gone:
                db.delete(item)
            db.commit()
        names = [i.text for i in gone]
        return SkillResult(f"Removed {', '.join(names)}." if names else "None of those were on the list.",
                           {"removed": names})


class ShowListArgs(BaseModel):
    list_name: str = "groceries"


class ShowList:
    name, input_schema, permission = "list_show", ShowListArgs, Permission.READ
    description = ("Read out a list ('what's on my grocery list'). To buy it, follow with the shopping or "
                   "order_from_link skill; ordering always stops before payment.")

    async def run(self, args: ShowListArgs, ctx: RunContext) -> SkillResult:
        name = life.list_name(args.list_name)
        with SessionLocal() as db:
            items = [i.text for i in life.items_on(db, ctx.user_id, name)]
        return SkillResult(f"Your {name} list: {', '.join(items)}." if items else f"Your {name} list is empty.",
                           {"list": name, "items": items})


class ClearList:
    name, input_schema, permission = "list_clear", ShowListArgs, Permission.CHANGE
    description = "Empty a whole list."

    async def run(self, args: ShowListArgs, ctx: RunContext) -> SkillResult:
        name = life.list_name(args.list_name)
        with SessionLocal() as db:
            items = life.items_on(db, ctx.user_id, name)
            for item in items:
                db.delete(item)
            db.commit()
        return SkillResult(f"Cleared your {name} list ({len(items)} item(s)).", {"cleared": len(items)})


skills = [TrackPayment(), MarkPaid(), ListPayments(), StopTracking(), AddHabit(), LogHabit(), HabitStatus(),
          RemoveHabit(), AddToList(), RemoveFromList(), ShowList(), ClearList()]
