"""Shared data operations, used by both the REST routes and Claude's tools."""
from datetime import datetime, timedelta

from . import db

REPEATS = ("none", "daily", "weekly")


def _norm_dt(value: str) -> str:
    """Accept 'YYYY-MM-DDTHH:MM[:SS]' (or with a space) and return clean local ISO."""
    dt = datetime.fromisoformat(value.strip().replace(" ", "T"))
    if dt.tzinfo is not None:  # store everything as naive local time
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


def _norm_date(value: str) -> str:
    return datetime.fromisoformat(value.strip()).date().isoformat()


# ---------- reminders ----------
def create_reminder(text: str, due_at: str, repeat: str = "none") -> dict:
    if repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {REPEATS}")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders(text,due_at,repeat,created_at) VALUES(?,?,?,?)",
            (text.strip(), _norm_dt(due_at), repeat, db.now_iso()),
        )
        return dict(conn.execute("SELECT * FROM reminders WHERE id=?", (cur.lastrowid,)).fetchone())


REMINDER_SELECT = (
    "SELECT r.*, e.title AS event_title, e.start AS event_start, e.all_day AS event_all_day, "
    "e.location AS event_location, e.description AS event_description "
    "FROM reminders r LEFT JOIN events e ON e.id = r.event_id"
)


def list_reminders(include_done: bool = True) -> list[dict]:
    q = REMINDER_SELECT
    if not include_done:
        q += " WHERE r.done=0"
    with db.connect() as conn:
        return db.rows(conn.execute(q + " ORDER BY r.due_at"))


def update_reminder(rid: int, **fields) -> dict | None:
    allowed = {"text", "due_at", "repeat", "done"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "due_at":
            v = _norm_dt(v)
            sets.append("sent_at=NULL")  # rescheduled -> eligible to notify again
        if k == "repeat" and v not in REPEATS:
            raise ValueError(f"repeat must be one of {REPEATS}")
        if k == "done":
            v = int(bool(v))
        sets.append(f"{k}=?")
        vals.append(v)
    with db.connect() as conn:
        if sets:
            conn.execute(f"UPDATE reminders SET {', '.join(sets)} WHERE id=?", (*vals, rid))
        r = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def complete_reminder(rid: int) -> dict | None:
    """Mark done; repeating reminders roll forward instead of finishing."""
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        if r["repeat"] != "none":
            roll_forward(conn, dict(r))
        else:
            conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
        return dict(conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone())


def roll_forward(conn, r: dict) -> None:
    """Move a repeating reminder's due_at to its next occurrence in the future."""
    step = timedelta(days=1 if r["repeat"] == "daily" else 7)
    due = datetime.fromisoformat(r["due_at"])
    now = datetime.now()
    while due <= now:
        due += step
    conn.execute(
        "UPDATE reminders SET due_at=?, sent_at=NULL, done=0 WHERE id=?",
        (due.isoformat(), r["id"]),
    )


def snooze_reminder(rid: int, minutes: int) -> dict | None:
    due = (datetime.now() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
    return update_reminder(rid, due_at=due, done=False)


def delete_reminder(rid: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM reminders WHERE id=?", (rid,))


# ---------- events ----------
def _event_reminder_due(start: str, all_day: bool, remind_min: int) -> str | None:
    """When the auto-reminder for an event should fire (local ISO), or None if it shouldn't exist.

    All-day events count from 09:00 on their day. If the lead time has already passed
    but the event hasn't started, remind straight away.
    """
    if remind_min < 0:
        return None
    base = datetime.fromisoformat(start[:10] + "T09:00") if all_day else datetime.fromisoformat(start)
    now = datetime.now()
    if base <= now:
        return None  # already started - nothing left to remind about
    return max(base - timedelta(minutes=remind_min), now).replace(microsecond=0).isoformat()


def _sync_event_reminder(conn, ev: dict, timing_changed: bool = True) -> None:
    """Keep the linked reminder for an event in step with the event.

    timing_changed=False (e.g. only the title was edited) leaves due/sent/done alone so
    an already-sent reminder isn't sent again, and doesn't resurrect one you deleted.
    """
    existing = conn.execute("SELECT * FROM reminders WHERE event_id=?", (ev["id"],)).fetchone()
    if ev["remind_min"] < 0:
        conn.execute("DELETE FROM reminders WHERE event_id=?", (ev["id"],))
        return
    if existing and not timing_changed:
        conn.execute("UPDATE reminders SET text=? WHERE event_id=?", (ev["title"], ev["id"]))
        return
    due = _event_reminder_due(ev["start"], bool(ev["all_day"]), ev["remind_min"])
    if due is None:
        return
    if existing:
        conn.execute("UPDATE reminders SET text=?, due_at=?, sent_at=NULL, done=0 WHERE event_id=?",
                     (ev["title"], due, ev["id"]))
    else:
        conn.execute(
            "INSERT INTO reminders(text,due_at,repeat,created_at,event_id) VALUES(?,?,?,?,?)",
            (ev["title"], due, "none", db.now_iso(), ev["id"]))


def create_event(title: str, start: str, end: str | None = None, all_day: bool = False,
                 location: str = "", description: str = "", remind_min: int = 15) -> dict:
    norm = _norm_date if all_day else _norm_dt
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO events(title,start,end,all_day,location,description,remind_min) VALUES(?,?,?,?,?,?,?)",
            (title.strip(), norm(start), norm(end) if end else None, int(all_day), location, description, remind_min),
        )
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone())
        _sync_event_reminder(conn, ev)
        return ev


def list_events(start: str | None = None, end: str | None = None) -> list[dict]:
    """Events whose start date falls in [start, end] (YYYY-MM-DD, inclusive)."""
    q, args = "SELECT * FROM events", []
    conds = []
    if start:
        conds.append("substr(start,1,10) >= ?")
        args.append(start)
    if end:
        conds.append("substr(start,1,10) <= ?")
        args.append(end)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    with db.connect() as conn:
        return db.rows(conn.execute(q + " ORDER BY start", args))


def update_event(eid: int, **fields) -> dict | None:
    allowed = {"title", "start", "end", "all_day", "location", "description", "remind_min"}
    with db.connect() as conn:
        before = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not before:
            return None
        all_day = bool(fields["all_day"]) if fields.get("all_day") is not None else bool(before["all_day"])
        norm = _norm_date if all_day else _norm_dt
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k in ("start", "end"):
                v = norm(v) if v else None
            if k == "all_day":
                v = int(bool(v))
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", (*vals, eid))
        after = dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
        timing_changed = any(before[k] != after[k] for k in ("start", "all_day", "remind_min"))
        _sync_event_reminder(conn, after, timing_changed)
        return after


def delete_event(eid: int) -> bool:
    """Delete an event and its auto-created reminder. Returns False if it didn't exist."""
    with db.connect() as conn:
        conn.execute("DELETE FROM reminders WHERE event_id=?", (eid,))
        return conn.execute("DELETE FROM events WHERE id=?", (eid,)).rowcount > 0


# ---------- date notes ----------
def get_note(date: str) -> dict:
    date = _norm_date(date)
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM date_notes WHERE date=?", (date,)).fetchone()
    return dict(r) if r else {"date": date, "body": "", "updated_at": None}


def set_note(date: str, body: str, append: bool = False) -> dict:
    date = _norm_date(date)
    if append:
        existing = get_note(date)["body"]
        body = (existing + "\n" + body).strip() if existing else body
    with db.connect() as conn:
        if body.strip():
            conn.execute(
                "INSERT INTO date_notes(date,body,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (date, body, db.now_iso()),
            )
        else:
            conn.execute("DELETE FROM date_notes WHERE date=?", (date,))
    return get_note(date)


def list_notes(start: str, end: str) -> list[dict]:
    with db.connect() as conn:
        return db.rows(conn.execute(
            "SELECT * FROM date_notes WHERE date BETWEEN ? AND ? ORDER BY date", (start, end)))


# ---------- combined ----------
def upcoming(days: int = 14) -> dict:
    today = datetime.now().date()
    end = (today + timedelta(days=days)).isoformat()
    return {
        "events": list_events(today.isoformat(), end),
        "reminders": [r for r in list_reminders(include_done=False) if r["due_at"][:10] <= end],
    }


# ---------- notebook (free-form notes + checklists) ----------
import re as _re

_HHMM = _re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RESET_MODES = ("manual", "daily")


def _attach_items(conn, pages: list[dict]) -> list[dict]:
    ids = [p["id"] for p in pages if p["kind"] == "checklist"]
    by_note: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        q = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT * FROM checklist_items WHERE note_id IN ({q}) ORDER BY position, id", ids):
            by_note[r["note_id"]].append(dict(r))
    for p in pages:
        if p["kind"] == "checklist":
            p["items"] = by_note[p["id"]]
    return pages


def create_page(body: str = "", kind: str = "text") -> dict:
    if kind not in ("text", "checklist"):
        raise ValueError("kind must be 'text' or 'checklist'")
    now = db.now_iso()
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes(body,created_at,updated_at,kind,last_reset) VALUES(?,?,?,?,?)",
            (body, now, now, kind, now))
        row = dict(conn.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone())
        return _attach_items(conn, [row])[0]


def list_pages(q: str | None = None) -> list[dict]:
    apply_due_resets()  # make sure a missed daily reset is applied before anyone looks at the list
    sql, args = "SELECT * FROM notes", []
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += (" WHERE body LIKE ? ESCAPE '\\' OR id IN "
                "(SELECT note_id FROM checklist_items WHERE text LIKE ? ESCAPE '\\' OR comment LIKE ? ESCAPE '\\')")
        args += [like, like, like]
    with db.connect() as conn:
        pages = db.rows(conn.execute(sql + " ORDER BY updated_at DESC, id DESC", args))
        return _attach_items(conn, pages)


def get_page(pid: int) -> dict | None:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM notes WHERE id=?", (pid,)).fetchone()
        return _attach_items(conn, [dict(r)])[0] if r else None


def update_page(pid: int, body: str) -> dict | None:
    with db.connect() as conn:
        conn.execute("UPDATE notes SET body=?, updated_at=? WHERE id=?", (body, db.now_iso(), pid))
    return get_page(pid)


def delete_page(pid: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM checklist_items WHERE note_id=?", (pid,))
        conn.execute("DELETE FROM checklist_log WHERE note_id=?", (pid,))
        conn.execute("DELETE FROM notes WHERE id=?", (pid,))


# ----- checklists -----
def set_checklist_settings(pid: int, reset_mode: str, reset_time: str) -> dict | None:
    if reset_mode not in RESET_MODES:
        raise ValueError(f"reset_mode must be one of {RESET_MODES}")
    if not _HHMM.match(reset_time):
        raise ValueError("reset_time must be HH:MM (24-hour)")
    with db.connect() as conn:
        # last_reset=now so changing the schedule never triggers an instant surprise reset
        conn.execute("UPDATE notes SET reset_mode=?, reset_time=?, last_reset=? WHERE id=? AND kind='checklist'",
                     (reset_mode, reset_time, db.now_iso(), pid))
    return get_page(pid)


def _log_day(moment: datetime) -> str:
    """Which day a reset at `moment` closes out. Shifting back 12h means a 06:30 reset closes
    yesterday, a 23:00 reset or an afternoon manual reset closes today."""
    return (moment - timedelta(hours=12)).date().isoformat()


def _snapshot(conn, pid: int, moment: datetime) -> None:
    """Save which items were ticked before a reset wipes them (history for the Progress tab).
    Two resets closing the same day merge: an item counts as done if it was ticked in either."""
    day = _log_day(moment)
    for it in conn.execute("SELECT id, text, done FROM checklist_items WHERE note_id=?", (pid,)).fetchall():
        if not it["text"].strip():
            continue
        conn.execute(
            "INSERT INTO checklist_log(day, note_id, item_id, text, done) VALUES (?,?,?,?,?) "
            "ON CONFLICT(day, note_id, item_id) DO UPDATE SET text=excluded.text, done=MAX(done, excluded.done)",
            (day, pid, it["id"], it["text"], it["done"]))


def reset_checklist(pid: int) -> dict | None:
    with db.connect() as conn:
        _snapshot(conn, pid, datetime.now())
        conn.execute("UPDATE checklist_items SET done=0 WHERE note_id=?", (pid,))
        conn.execute("UPDATE notes SET last_reset=? WHERE id=?", (db.now_iso(), pid))
    return get_page(pid)


def apply_due_resets() -> int:
    """Untick every daily checklist whose most recent reset time has passed since its last reset.

    Based on 'last reset < most recent scheduled moment', so a laptop that was asleep or off
    at the reset time still gets its reset on the next run. Returns how many were reset.
    """
    now = datetime.now()
    count = 0
    with db.connect() as conn:
        for p in conn.execute("SELECT * FROM notes WHERE kind='checklist' AND reset_mode='daily'").fetchall():
            h, m = (int(x) for x in p["reset_time"].split(":"))
            slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot > now:
                slot -= timedelta(days=1)
            last = datetime.fromisoformat(p["last_reset"]) if p["last_reset"] else None
            if last is None or last < slot:
                _snapshot(conn, p["id"], slot)
                conn.execute("UPDATE checklist_items SET done=0 WHERE note_id=?", (p["id"],))
                conn.execute("UPDATE notes SET last_reset=? WHERE id=?", (db.now_iso(), p["id"]))
                count += 1
    return count


def _renumber(conn, ids: list[int]) -> None:
    for pos, iid in enumerate(ids):
        conn.execute("UPDATE checklist_items SET position=? WHERE id=?", (pos, iid))


def add_item(pid: int, text: str = "", after_id: int | None = None) -> dict | None:
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM notes WHERE id=? AND kind='checklist'", (pid,)).fetchone():
            return None
        cur = conn.execute("INSERT INTO checklist_items(note_id,text,position) VALUES(?,?,?)", (pid, text, 10**9))
        new_id = cur.lastrowid
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM checklist_items WHERE note_id=? AND id<>? ORDER BY position, id", (pid, new_id))]
        at = ids.index(after_id) + 1 if after_id in ids else len(ids)
        ids.insert(at, new_id)
        _renumber(conn, ids)
        conn.execute("UPDATE notes SET updated_at=? WHERE id=?", (db.now_iso(), pid))
        return dict(conn.execute("SELECT * FROM checklist_items WHERE id=?", (new_id,)).fetchone())


def update_item(iid: int, **fields) -> dict | None:
    sets, vals = [], []
    for k in ("text", "comment", "done"):
        if fields.get(k) is None:
            continue
        sets.append(f"{k}=?")
        vals.append(int(bool(fields[k])) if k == "done" else fields[k])
    with db.connect() as conn:
        if sets:
            conn.execute(f"UPDATE checklist_items SET {', '.join(sets)} WHERE id=?", (*vals, iid))
            if "text" in fields or "comment" in fields:  # ticking boxes shouldn't reshuffle the note list
                conn.execute("UPDATE notes SET updated_at=? WHERE id=(SELECT note_id FROM checklist_items WHERE id=?)",
                             (db.now_iso(), iid))
        r = conn.execute("SELECT * FROM checklist_items WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None


def delete_item(iid: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM checklist_items WHERE id=?", (iid,))


def reorder_items(pid: int, ids: list[int]) -> None:
    with db.connect() as conn:
        existing = [r["id"] for r in conn.execute(
            "SELECT id FROM checklist_items WHERE note_id=? ORDER BY position, id", (pid,))]
        if sorted(ids) != sorted(existing):
            raise ValueError("ids must be exactly this checklist's items")
        _renumber(conn, ids)


# ----- progress (history of a checklist) -----
def _title(body: str) -> str:
    return (body or "").strip().split("\n")[0].strip() or "Untitled checklist"


def progress(note_id: int | None = None, weeks: int = 53) -> dict:
    """Per-day done/total plus the names of ticked items for one checklist.

    Today's still-open list is included live (it belongs to the day the next reset will close).
    Defaults to the checklist whose title contains 'daily', else a daily-reset one, else the first.
    """
    with db.connect() as conn:
        lists = [dict(r) for r in conn.execute("SELECT * FROM notes WHERE kind='checklist' ORDER BY id")]
        if not lists:
            return {"checklists": [], "selected": None, "days": {}}
        chosen = next((l for l in lists if l["id"] == note_id), None) \
            or next((l for l in lists if "daily" in _title(l["body"]).lower()), None) \
            or next((l for l in lists if l["reset_mode"] == "daily"), None) \
            or lists[0]
        pid = chosen["id"]
        days: dict[str, dict] = {}
        for r in conn.execute("SELECT day, text, done FROM checklist_log WHERE note_id=? ORDER BY item_id", (pid,)):
            d = days.setdefault(r["day"], {"total": 0, "done": []})
            d["total"] += 1
            if r["done"]:
                d["done"].append(r["text"])
        items = [dict(r) for r in conn.execute(
            "SELECT id, text, done FROM checklist_items WHERE note_id=? AND trim(text)<>'' ORDER BY position, id", (pid,))]
    # live state -> the day the next reset closes
    now = datetime.now()
    nxt = now
    if chosen["reset_mode"] == "daily":
        h, m = (int(x) for x in chosen["reset_time"].split(":"))
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    live_day = _log_day(nxt)
    if items:
        live = days.setdefault(live_day, {"total": 0, "done": []})
        logged = {n for n in live["done"]}
        live["total"] = max(live["total"], len(items))
        for it in items:
            if it["done"] and it["text"] not in logged:
                live["done"].append(it["text"])
    oldest = (now.date() - timedelta(weeks=weeks)).isoformat()
    return {
        "checklists": [{"id": l["id"], "title": _title(l["body"])} for l in lists],
        "selected": pid,
        "live_day": live_day,
        "days": {k: v for k, v in days.items() if k >= oldest and v["total"] > 0},
    }
