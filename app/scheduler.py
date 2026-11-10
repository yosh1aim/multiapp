"""Sends due reminders to the phone. Runs once at startup (catch-up) and every 30s after."""
import asyncio
import logging
from datetime import datetime

from . import db, notify, services

log = logging.getLogger("multiapp.scheduler")

TICK_SECONDS = 30
MISSED_AFTER_MINUTES = 5


def _format_due(due: datetime) -> str:
    if due.date() == datetime.now().date():
        return due.strftime("%H:%M")
    return due.strftime("%a %d %b, %H:%M")


def _event_body(event_id: int, fallback: str) -> str:
    """Notification text for an event-linked reminder: start time, place, and the event's notes."""
    with db.connect() as conn:
        e = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        return fallback
    if e["all_day"]:
        when = "All day"
    else:
        when = "Starts " + _format_due(datetime.fromisoformat(e["start"]))
    head = " · ".join(x for x in (when, e["location"]) if x)
    return head + ("\n" + e["description"].strip() if e["description"].strip() else "")


def send_due_reminders() -> int:
    """Notify every unsent, undone reminder that is due. Returns how many were sent."""
    now = datetime.now().replace(microsecond=0).isoformat()
    with db.connect() as conn:
        due = db.rows(conn.execute(
            "SELECT * FROM reminders WHERE done=0 AND sent_at IS NULL AND due_at <= ? ORDER BY due_at",
            (now,)))
    sent = 0
    for r in due:
        due_dt = datetime.fromisoformat(r["due_at"])
        late_min = (datetime.now() - due_dt).total_seconds() / 60
        missed = late_min > MISSED_AFTER_MINUTES
        title = f"Missed: {r['text']}" if missed else r["text"]
        body = f"Was due {_format_due(due_dt)}" if missed else f"Due {_format_due(due_dt)}"
        tags, priority = (["hourglass_flowing_sand"] if missed else ["bell"]), 4
        if r.get("event_id"):
            body = _event_body(r["event_id"], body)
            tags = tags + ["calendar"]
        if not notify.send(title, body, priority=priority, tags=tags):
            log.warning("ntfy send failed for reminder %s; will retry", r["id"])
            continue  # leave unsent -> retried next tick (e.g. laptop offline)
        sent += 1
        with db.connect() as conn:
            if r["repeat"] != "none":
                services.roll_forward(conn, r)
            else:
                conn.execute("UPDATE reminders SET sent_at=? WHERE id=?", (db.now_iso(), r["id"]))
    return sent


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(services.apply_due_resets)  # daily checklists (also catches up after sleep)
            n = await asyncio.to_thread(send_due_reminders)
            if n:
                log.info("sent %d reminder(s)", n)
        except Exception:  # never let the loop die
            log.exception("scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)
