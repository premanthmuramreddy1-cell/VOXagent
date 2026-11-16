"""Everyday life, carried over from J.A.R.V.I.S. and made per-user and time-zone aware: subscriptions and bills
(track, nudge before they fall due, never pay), daily habits (targets and timed doses, nudged only when behind) and
shopping lists. The agent's model turns any phrasing into the structured calls below, so there is no phrase list."""

import calendar
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Habit, ListItem, Payment

QUIET_FROM, QUIET_UNTIL = 22, 7  # no habit nudges at night
PER_MONTH = {"week": 52 / 12, "month": 1.0, "year": 1 / 12}
# shortcut: the common zones only; an unknown zone keeps the bare number instead of guessing a currency
ZONE_CURRENCY = {"Asia/Kolkata": "₹", "Asia/Calcutta": "₹", "Europe/London": "£", "Asia/Tokyo": "¥",
                 "America/": "$", "Europe/": "€", "Australia/": "A$", "Asia/Singapore": "S$", "Asia/Dubai": "AED"}


def local_currency(tz: str) -> str:
    """The currency a bare amount most likely means, from the user's time zone (exact zone beats region)."""
    return ZONE_CURRENCY.get(tz) or next((c for k, c in ZONE_CURRENCY.items() if k.endswith("/") and tz.startswith(k)), "")


# ---------------------------------------------------------------- money

def count(n: int, word: str) -> str:
    """'1 item', '2 items', '1 glass', '8 glasses': spoken text never says 'item(s)'."""
    plural = word + ("es" if word.endswith(("s", "x", "ch", "sh")) else "s")
    return f"{n} {word if n == 1 else plural}"


def money(amount: float, currency: str = "") -> str:
    text = f"{amount:,.2f}".removesuffix(".00")
    return f"{currency}{text}" if len(currency) <= 1 else f"{text} {currency}"


def add_cycle(day: date, cycle: str, anchor: int = 0) -> date:
    """The next due date one cycle later; monthly and yearly dates keep their anchor day (31 -> 28 -> 31)."""
    if cycle == "week":
        return day + timedelta(days=7)
    months = 12 if cycle == "year" else 1
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    return date(year, month, min(anchor or day.day, calendar.monthrange(year, month)[1]))


def first_due(today: date, day_of_month: int) -> date:
    """'renews on the 5th' -> the next 5th from today (today counts)."""
    last = calendar.monthrange(today.year, today.month)[1]
    this_month = date(today.year, today.month, min(day_of_month, last))
    return this_month if this_month >= today else add_cycle(this_month, "month", day_of_month)


def find_payment(db: Session, user_id: str, name: str) -> Payment | None:
    rows = list(db.scalars(select(Payment).where(Payment.user_id == user_id)))
    wanted = name.strip().lower()
    if not wanted:
        return None
    return (next((p for p in rows if p.name.lower() == wanted), None)
            or next((p for p in rows if wanted and (wanted in p.name.lower() or p.name.lower() in wanted)), None))


def mark_paid(payment: Payment, today: date) -> None:
    due = date.fromisoformat(payment.next_due)
    payment.last_paid = today.isoformat()
    payment.next_due = add_cycle(due, payment.cycle, payment.day).isoformat()


def monthly_totals(payments: list[Payment]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for p in payments:
        totals[p.currency] = totals.get(p.currency, 0) + p.amount * PER_MONTH.get(p.cycle, 1)
    return totals


def payment_nudges(db: Session, user_id: str, today: date) -> list[str]:
    """At most one nudge per item per day: N days before, on the day, and daily while a bill is unpaid.
    Subscriptions and autopay bills renew by themselves, so once their day has passed they roll forward quietly."""
    out = []
    for p in db.scalars(select(Payment).where(Payment.user_id == user_id)):
        due = date.fromisoformat(p.next_due)
        while due < today and (p.autopay or p.kind == "subscription"):
            due = add_cycle(due, p.cycle, p.day)
            p.next_due = due.isoformat()
        key = f"{p.next_due}:{today.isoformat()}"
        days = (due - today).days
        if p.nudged == key or not (days in (p.remind_days, 0) or days < 0):
            continue
        cost = f" ({money(p.amount, p.currency)})" if p.amount else ""
        if days < 0:
            out.append(f"{p.name} was due {count(-days, 'day')} ago and is not marked paid. Say 'I paid {p.name}' once it is.")
        else:
            when = "today" if days == 0 else f"in {days} day{'s' * (days != 1)}"
            out.append(f"{p.name} {'renews' if p.kind == 'subscription' else 'is due'} {when}{cost}.")
        p.nudged = key
    db.commit()
    return out


def payment_lines(db: Session, user_id: str, today: date, within: int = 3) -> list[str]:
    soon = []
    for p in db.scalars(select(Payment).where(Payment.user_id == user_id).order_by(Payment.next_due)):
        days = (date.fromisoformat(p.next_due) - today).days
        if days <= within:
            label = ("today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days" if days > 0
                     else "overdue")
            soon.append(f"{p.name} {label}" + (f" ({money(p.amount, p.currency)})" if p.amount else ""))
    return [f"Due soon: {'; '.join(soon)}."] if soon else []


# ---------------------------------------------------------------- habits

def done_on(habit: Habit, day: date) -> int:
    return int((habit.log or {}).get(day.isoformat(), 0))


def streak(habit: Habit, today: date) -> int:
    """Days in a row the target was met, counting today only once it is met."""
    day, count = today, 0
    if done_on(habit, day) < habit.target:
        day -= timedelta(days=1)
    while done_on(habit, day) >= habit.target:
        count, day = count + 1, day - timedelta(days=1)
    return count


def find_habit(db: Session, user_id: str, name: str) -> Habit | None:
    rows = list(db.scalars(select(Habit).where(Habit.user_id == user_id)))
    wanted = name.strip().lower()
    if not wanted:
        return None
    return (next((h for h in rows if h.name.lower() == wanted), None)
            or next((h for h in rows if wanted and (wanted in h.name.lower() or h.name.lower() in wanted)), None))


def log_habit(habit: Habit, day: date, count: int) -> int:
    log = dict(habit.log or {})  # a new dict, so the JSON column sees the change
    log[day.isoformat()] = max(0, int(log.get(day.isoformat(), 0)) + count)
    habit.log = log
    return int(log[day.isoformat()])


def habit_nudges(db: Session, user_id: str, now: datetime) -> list[str]:
    """Timed habits: once per slot that has passed without being logged. Target habits: only when behind the
    even pace between 09:00 and 21:00, at most once per three-hour block. Never at night."""
    if now.hour >= QUIET_FROM or now.hour < QUIET_UNTIL:
        return []
    today, clock, out = now.date(), now.strftime("%H:%M"), []
    for h in db.scalars(select(Habit).where(Habit.user_id == user_id)):
        done, slot, message = done_on(h, today), "", ""
        passed = [t for t in sorted(h.times or []) if t <= clock]
        if passed and done < len(passed):
            slot, message = passed[-1], f"Time for your {h.name} ({passed[-1]})."
        elif not h.times and h.target > 1 and 9 <= now.hour < 21:
            expected = int(h.target * (now.hour - 9 + now.minute / 60) / 12)
            if done < expected - 1:
                slot = f"pace{now.hour // 3}"
                message = f"{h.name}: {done} of {h.target} {h.unit} so far, a little behind for this time of day."
        key = f"{today.isoformat()}:{slot}"
        if slot and not (h.nudged or {}).get(key):
            h.nudged = {k: v for k, v in (h.nudged or {}).items() if k.startswith(today.isoformat())} | {key: True}
            out.append(message)
    db.commit()
    return out


def habit_lines(db: Session, user_id: str, today: date) -> list[str]:
    parts = []
    for h in db.scalars(select(Habit).where(Habit.user_id == user_id)):
        run = streak(h, today)
        parts.append(f"{h.name} {done_on(h, today)}/{h.target}" + (f", {run}-day streak" if run > 1 else ""))
    return [f"Habits today: {'; '.join(parts)}."] if parts else []


# ---------------------------------------------------------------- lists

def list_name(name: str) -> str:
    """'grocery', 'groceries', 'shopping' and nothing all mean the grocery list; 'packing list' -> 'packing'."""
    key = name.strip().lower().removesuffix(" list").strip()
    return "groceries" if key in ("", "grocery", "groceries", "shopping", "grocery shopping", "food") else key


def items_on(db: Session, user_id: str, name: str) -> list[ListItem]:
    return list(db.scalars(select(ListItem).where(ListItem.user_id == user_id, ListItem.list == list_name(name))
                           .order_by(ListItem.created_at)))


def list_lines(db: Session, user_id: str) -> list[str]:
    counts: dict[str, int] = {}
    for row in db.scalars(select(ListItem).where(ListItem.user_id == user_id)):
        counts[row.list] = counts.get(row.list, 0) + 1
    return [f"Lists: {', '.join(f'{n} on {k}' for k, n in sorted(counts.items()))}."] if counts else []
