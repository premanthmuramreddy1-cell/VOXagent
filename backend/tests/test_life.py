import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app import background, life, personal, push
from sqlalchemy import select

from app.db import Habit, Payment, SessionLocal, User, new_id
from app.skills import RunContext
from app.skills import life_tools as lt


def user() -> str:
    with SessionLocal() as db:
        row = User(email=f"l{new_id()}@example.com", password_hash="x")
        db.add(row)
        db.commit()
        return row.id


def run(skill, uid: str, **args):
    ctx = RunContext(uid, "s", "", [], AsyncMock(), AsyncMock())
    return asyncio.run(skill.run(skill.input_schema(**args), ctx))


@pytest.mark.parametrize(("start", "cycle", "anchor", "expected"), [
    (date(2026, 1, 31), "month", 31, date(2026, 2, 28)),
    (date(2026, 2, 28), "month", 31, date(2026, 3, 31)),  # keeps the 31st after a short month
    (date(2026, 12, 15), "month", 15, date(2027, 1, 15)),
    (date(2028, 2, 29), "year", 29, date(2029, 2, 28)),
    (date(2026, 11, 16), "week", 0, date(2026, 11, 23)),
])
def test_add_cycle_keeps_the_anchor_day(start, cycle, anchor, expected):
    assert life.add_cycle(start, cycle, anchor) == expected


def test_first_due_is_the_next_matching_day():
    assert life.first_due(date(2026, 11, 16), 5) == date(2026, 12, 5)
    assert life.first_due(date(2026, 11, 16), 16) == date(2026, 11, 16)
    assert life.first_due(date(2026, 2, 10), 31) == date(2026, 2, 28)


def test_track_list_and_pay_with_monthly_totals_per_currency():
    uid = user()
    run(lt.TrackPayment(), uid, name="Netflix", kind="subscription", amount=649, currency="₹", next_due="2030-01-05")
    run(lt.TrackPayment(), uid, name="Domain", kind="subscription", amount=1200, currency="₹", cycle="year",
        next_due="2030-03-01")
    run(lt.TrackPayment(), uid, name="iCloud", kind="subscription", amount=3, currency="$", next_due="2030-01-09")
    again = run(lt.TrackPayment(), uid, name="netflix", kind="subscription", amount=799, next_due="2030-01-05")
    assert "₹799" in again.summary  # same name updates instead of duplicating, keeping the currency
    listed = run(lt.ListPayments(), uid)
    assert len(listed.data["items"]) == 3 and "About ₹899 + $3 a month" in listed.summary
    assert run(lt.MarkPaid(), uid, name="netflix").data["next_due"] == "2030-02-05"
    assert "not tracking" in run(lt.MarkPaid(), uid, name="Spotify").summary


def test_payment_nudges_fire_once_per_day_and_follow_up_unpaid_bills():
    uid = user()
    with SessionLocal() as db:
        db.add(Payment(user_id=uid, kind="bill", name="Electricity", amount=1500, currency="₹",
                       next_due="2030-01-10", day=10, remind_days=2))
        db.add(Payment(user_id=uid, kind="subscription", name="Spotify", amount=119, currency="₹",
                       next_due="2030-01-05", day=5))
        db.commit()
        assert life.payment_nudges(db, uid, date(2030, 1, 7)) == []  # too early; Spotify renewed by itself
        assert life.payment_nudges(db, uid, date(2030, 1, 8)) == ["Electricity is due in 2 days (₹1,500)."]
        assert life.payment_nudges(db, uid, date(2030, 1, 8)) == []  # once a day, not every 5 minutes
        assert life.payment_nudges(db, uid, date(2030, 1, 10)) == ["Electricity is due today (₹1,500)."]
        assert "was due 2 days ago" in life.payment_nudges(db, uid, date(2030, 1, 12))[0]  # unpaid keeps asking
        spotify = life.find_payment(db, uid, "spotify")
        assert spotify is not None and spotify.next_due == "2030-02-05"


def test_habits_log_streak_and_status():
    uid = user()
    run(lt.AddHabit(), uid, name="Water", target=8, unit="glass")
    with SessionLocal() as db:
        habit = life.find_habit(db, uid, "water")
        assert habit is not None
        today = personal.local_now(db, uid).date()
        habit.log = {(today - timedelta(days=1)).isoformat(): 8, (today - timedelta(days=2)).isoformat(): 9}
        db.commit()
    logged = run(lt.LogHabit(), uid, name="water", count=3)
    assert logged.data == {"done": 3, "target": 8, "streak": 2} and "5 to go" in logged.summary
    assert run(lt.LogHabit(), uid, name="water", count=5).data["streak"] == 3  # today counts once the target is met
    assert "Water 8/8, 3-day streak" in run(lt.HabitStatus(), uid).summary
    assert "not tracking" in run(lt.LogHabit(), uid, name="yoga").summary


def test_habit_nudges_respect_times_pace_and_quiet_hours():
    uid = user()
    run(lt.AddHabit(), uid, name="Vitamin D", times=["9:00", "21:00"], unit="dose")
    run(lt.AddHabit(), uid, name="Water", target=8, unit="glass")
    with SessionLocal() as db:
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 8, 30)) == []
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 9, 5)) == ["Time for your Vitamin D (09:00)."]
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 9, 30)) == []  # one nudge per slot
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 15, 0)) == [
            "Water: 0 of 8 glass so far, a little behind for this time of day."]
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 23, 0)) == []  # quiet at night
        vitamin = life.find_habit(db, uid, "vitamin")
        assert vitamin is not None
        life.log_habit(vitamin, date(2030, 1, 1), 2)
        db.commit()
        assert life.habit_nudges(db, uid, datetime(2030, 1, 1, 21, 10)) == []  # both doses taken: nothing to nag


def test_lists_dedupe_alias_remove_and_clear():
    uid = user()
    added = run(lt.AddToList(), uid, items=["Milk", "eggs", "milk"], list_name="shopping")
    assert added.data == {"added": ["Milk", "eggs"], "list": "groceries"}
    assert run(lt.AddToList(), uid, items=["MILK"], list_name="grocery list").data["added"] == []
    run(lt.AddToList(), uid, items=["passport"], list_name="packing list")
    assert run(lt.ShowList(), uid).data["items"] == ["Milk", "eggs"]
    assert run(lt.RemoveFromList(), uid, items=["egg"]).data["removed"] == ["eggs"]
    assert run(lt.ShowList(), uid, list_name="packing").data["items"] == ["passport"]
    assert run(lt.ClearList(), uid, list_name="groceries").data["cleared"] == 1
    assert "empty" in run(lt.ShowList(), uid).summary


def test_briefing_plans_the_day_with_bills_habits_and_lists():
    uid = user()
    with SessionLocal() as db:
        today = personal.local_now(db, uid).date()
        db.add(Payment(user_id=uid, kind="bill", name="Rent", amount=15000, currency="₹", next_due=today.isoformat()))
        db.add(Habit(user_id=uid, name="Water", target=8, unit="glass"))
        db.commit()
    run(lt.AddToList(), uid, items=["coffee"])
    with SessionLocal() as db:
        text = personal.briefing(db, uid)
    assert "Due soon: Rent today (₹15,000)." in text and "Water 0/8" in text and "1 on groceries" in text


def test_background_pushes_life_nudges_once(monkeypatch):
    uid = user()
    with SessionLocal() as db:
        db.add(Payment(user_id=uid, kind="bill", name="Phone", amount=499, currency="₹", next_due="2030-01-12",
                       remind_days=2))
        db.commit()
    sent = AsyncMock()
    monkeypatch.setattr(push, "notify", sent)
    at = datetime.fromisoformat("2030-01-10T10:00:00+00:00")
    asyncio.run(background.send_life_nudges(at))
    assert any(c.args[0] == uid and "Phone is due in 2 days" in c.args[2] for c in sent.await_args_list)
    sent.reset_mock()
    asyncio.run(background.send_life_nudges(at))
    assert not any(c.args[0] == uid for c in sent.await_args_list)  # not twice the same day


def test_bare_amount_takes_the_local_currency_and_unknown_zone_guesses_nothing():
    assert life.local_currency("Asia/Kolkata") == "₹" and life.local_currency("Europe/London") == "£"
    assert life.local_currency("Europe/Berlin") == "€" and life.local_currency("UTC") == ""
    uid = user()
    with SessionLocal() as db:
        personal.user_settings(db, uid).tz = "Asia/Kolkata"
        db.commit()
    done = run(lt.TrackPayment(), uid, name="Electricity", kind="bill", amount=1500, day_of_month=10)
    assert "₹1,500" in done.summary


def test_usual_order_saves_replaces_and_reorders_through_the_checkout_stop(monkeypatch):
    from app import orders
    from app.skills import order_tools as ot
    uid = user()
    assert "need the link" in run(ot.SaveUsual(), uid, name="coffee").summary
    run(ot.SaveUsual(), uid, name="Coffee", request="1 cold coffee", url="https://shop.example/menu")
    run(ot.SaveUsual(), uid, name="coffee", request="2 cold coffee", url="https://shop.example/menu")
    seen = {}

    async def prepare(user_id, url, request, profile, complete, emit=None):
        seen.update(url=url, request=request)
        return {"status": "ready", "note": "", "url": url, "steps": [], "total": "₹380",
                "final_button": "Place order", "screenshot": ""}

    monkeypatch.setattr(orders, "prepare", prepare)
    result = run(ot.OrderUsual(), uid, name="my usual coffee")
    assert seen == {"url": "https://shop.example/menu", "request": "2 cold coffee"}  # replaced, not duplicated
    assert "say yes" in result.summary  # still stops before the final button
    assert "no usual called tea" in run(ot.OrderUsual(), uid, name="tea").summary


def test_bad_input_gets_a_question_instead_of_a_crash_or_junk_rows():
    uid = user()
    assert "couldn't read 'next friday'" in run(lt.TrackPayment(), uid, name="Rent", kind="bill",
                                                 next_due="next friday").summary
    assert "What should I call" in run(lt.TrackPayment(), uid, name="  ", kind="bill", day_of_month=5).summary
    assert "not tracking" in run(lt.MarkPaid(), uid, name="").summary  # a blank name matches nothing
    assert "couldn't read 25:99, noon" in run(lt.AddHabit(), uid, name="Vitamin", times=["25:99", "noon"]).summary
    assert "Tell me what to add" in run(lt.AddToList(), uid, items=["", "  "]).summary
    long = run(lt.AddToList(), uid, items=["x" * 5000])
    assert len(long.data["added"][0]) == 120
    with SessionLocal() as db:
        assert not list(db.scalars(select(Payment).where(Payment.user_id == uid)))
        assert not list(db.scalars(select(Habit).where(Habit.user_id == uid)))


def test_usual_name_with_separators_is_saved_under_a_clean_name():
    from app.skills import order_tools as ot
    uid = user()
    saved = run(ot.SaveUsual(), uid, name="Tea: green", request="1 tea | large", url=" https://shop.example/m ")
    assert saved.data == {"name": "tea green", "request": "1 tea | large", "url": "https://shop.example/m"}
    with SessionLocal() as db:
        assert list(ot._usuals(db, uid)) == ["tea green"]
    assert "What should I call" in run(ot.SaveUsual(), uid, name=" : ", request="x", url="https://a.example").summary


def test_spoken_counts_use_real_plurals():
    assert [life.count(1, "item"), life.count(2, "item"), life.count(8, "glass"), life.count(1, "day")] == [
        "1 item", "2 items", "8 glasses", "1 day"]
    uid = user()
    assert "has 1 item." in run(lt.AddToList(), uid, items=["milk"]).summary
    assert "8 glasses a day" in run(lt.AddHabit(), uid, name="Water", target=8, unit="glass").summary
