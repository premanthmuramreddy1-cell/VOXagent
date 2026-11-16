"""Background jobs started with the app: idle sandbox reaper, reminder delivery, daily briefing, Telegram polling."""

import asyncio
import contextlib
import logging
from datetime import datetime

from sqlalchemy import select

from app import forms, orders, personal, push, sandbox
from app.bots import telegram_running
from app.config import settings
from app.db import SessionLocal, UserSetting

log = logging.getLogger("vox.jobs")


async def deliver_reminders() -> int:
    """Push every due reminder once; recurring ones roll forward instead of finishing."""
    sent = 0
    with SessionLocal() as db:
        for reminder in personal.due_reminders(db):
            await push.notify(reminder.user_id, "Reminder", reminder.text, "/#/me")
            personal.fire(db, reminder)
            sent += 1
    return sent


async def send_briefings(at: datetime | None = None) -> int:
    """Once a day at VOX_BRIEFING_HOUR in each user's own time zone."""
    sent = 0
    with SessionLocal() as db:
        for row in db.scalars(select(UserSetting).where(UserSetting.briefing.is_(True))):
            local = (at or datetime.now()).astimezone(personal.zone(db, row.user_id))
            if local.hour != settings().briefing_hour or row.last_briefing == local.date().isoformat():
                continue
            await push.notify(row.user_id, "Your day", personal.briefing(db, row.user_id), "/#/me")
            row.last_briefing = local.date().isoformat()
            db.commit()
            sent += 1
    return sent


async def _every(seconds: int, job) -> None:
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:  # one failing run must not stop the schedule
            log.exception("background job failed")
        await asyncio.sleep(seconds)


@contextlib.asynccontextmanager
async def running(_app=None):
    tasks = [asyncio.create_task(_every(60, sandbox.reap_idle)),
             asyncio.create_task(_every(120, forms.reap)),
             asyncio.create_task(_every(120, orders.reap)),
             asyncio.create_task(_every(20, deliver_reminders)),
             asyncio.create_task(_every(300, send_briefings))]
    async with telegram_running():
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for ident in list(sandbox.SERVERS):
                await sandbox.stop_server(ident)
