"""Two-way sync between multiapp events and one calendar in macOS Calendar.app.

Only the calendar named CALENDAR_SYNC_NAME (default "multiapp") is touched. If it lives in
iCloud, Calendar.app pushes it to your phone. Uses AppleScript, so the first run asks for
permission to control Calendar.

Each linked event stores the Calendar uid plus a hash of the fields as of the last sync, so a
pass can tell which side changed. If both changed, multiapp wins. Deleting on either side
deletes on the other. Recurring events created in Calendar.app are skipped.
"""
import hashlib
import logging
import os
import subprocess
from datetime import datetime, timedelta

from . import db, services

log = logging.getLogger("multiapp.calsync")

FS, RS = "\x1f", "\x1e"      # field / record separators in AppleScript output
PAST_DAYS, FUTURE_DAYS = 60, 365
FIELDS = ("title", "start", "end", "all_day", "location", "description")


def calendar_name() -> str:
    return os.environ.get("CALENDAR_SYNC_NAME", "multiapp")


def enabled() -> bool:
    return os.environ.get("CALENDAR_SYNC", "1").strip().lower() not in ("0", "false", "no", "off")


# ---------- AppleScript plumbing ----------
def _osa(script: str, *args: str) -> str:
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "osascript failed")
    return r.stdout.rstrip("\n")


# Builds a date from numeric parts (locale-proof). Day is set to 1 first so month changes can't overflow.
_MKDATE = """
on mkdate(y, m, d, hh, mm)
    set dt to current date
    set day of dt to 1
    set year of dt to y
    set month of dt to m
    set day of dt to d
    set hours of dt to hh
    set minutes of dt to mm
    set seconds of dt to 0
    return dt
end mkdate
on parts(dt)
    return ((year of dt) as text) & "-" & ((month of dt as integer) as text) & "-" & ((day of dt) as text) & "-" & ((hours of dt) as text) & "-" & ((minutes of dt) as text)
end parts
"""

_READ = _MKDATE + """
on run argv
    set w1 to mkdate(item 1 of argv as integer, item 2 of argv as integer, item 3 of argv as integer, 0, 0)
    set w2 to mkdate(item 4 of argv as integer, item 5 of argv as integer, item 6 of argv as integer, 0, 0)
    set fs to ASCII character 31
    set rs to ASCII character 30
    set out to ""
    tell application "Calendar"
        if not (exists calendar (item 7 of argv)) then return ""
        tell calendar (item 7 of argv)
            set evs to (every event whose start date ≥ w1 and start date ≤ w2)
            repeat with e in evs
                if (recurrence of e) is missing value then
                    set loc to location of e
                    if loc is missing value then set loc to ""
                    set dsc to description of e
                    if dsc is missing value then set dsc to ""
                    set out to out & (uid of e) & fs & (summary of e) & fs & my parts(start date of e) & fs & my parts(end date of e) & fs & ((allday event of e) as text) & fs & loc & fs & dsc & rs
                end if
            end repeat
        end tell
    end tell
    return out
end run
"""

# argv: calname, uid ("" = create), title, sy sm sd sh sn, ey em ed eh en, allday, location, description
_WRITE = _MKDATE + """
on run argv
    set calname to item 1 of argv
    set uidIn to item 2 of argv
    set sd to my mkdate(item 4 of argv as integer, item 5 of argv as integer, item 6 of argv as integer, item 7 of argv as integer, item 8 of argv as integer)
    set ed to my mkdate(item 9 of argv as integer, item 10 of argv as integer, item 11 of argv as integer, item 12 of argv as integer, item 13 of argv as integer)
    set isAllDay to (item 14 of argv is "true")
    tell application "Calendar"
        tell calendar calname
            if uidIn is "" then
                set e to make new event with properties {summary:(item 3 of argv), start date:sd, end date:ed, allday event:isAllDay, location:(item 15 of argv), description:(item 16 of argv)}
            else
                set e to first event whose uid is uidIn
                set allday event of e to isAllDay
                try
                    set start date of e to sd
                on error  -- new start is after the old end: move the end first
                    set end date of e to ed
                    set start date of e to sd
                end try
                set end date of e to ed
                set summary of e to (item 3 of argv)
                set location of e to (item 15 of argv)
                set description of e to (item 16 of argv)
            end if
            return uid of e
        end tell
    end tell
end run
"""

_DELETE = """
on run argv
    tell application "Calendar"
        if not (exists calendar (item 1 of argv)) then return
        tell calendar (item 1 of argv)
            delete (every event whose uid is (item 2 of argv))
        end tell
    end tell
end run
"""

_ENSURE = """
on run argv
    tell application "Calendar"
        if not (exists calendar (item 1 of argv)) then make new calendar with properties {name:(item 1 of argv)}
    end tell
end run
"""


# ---------- field conversion ----------
def _parse_parts(s: str) -> datetime:
    y, m, d, hh, mm = (int(x) for x in s.split("-"))
    return datetime(y, m, d, hh, mm)


def _parts(dt: datetime) -> list[str]:
    return [str(dt.year), str(dt.month), str(dt.day), str(dt.hour), str(dt.minute)]


def _canon(f: dict) -> dict:
    """Fill in the end Calendar.app requires (timed: +1h, all-day: same day) so both sides compare equal."""
    all_day = bool(f["all_day"])
    start = f["start"][:10] if all_day else f["start"][:16]
    end = (f.get("end") or "")
    end = end[:10] if all_day else end[:16]
    if not end or end < start:
        end = start if all_day else (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="minutes")
    return {"title": f["title"].strip(), "start": start, "end": end, "all_day": int(all_day),
            "location": f.get("location") or "", "description": f.get("description") or ""}


def _hash(f: dict) -> str:
    c = _canon(f)
    return hashlib.sha1(FS.join(str(c[k]) for k in FIELDS).encode()).hexdigest()


def _from_mac(rec: list[str]) -> dict:
    uid, title, s, e, all_day, loc, desc = rec
    s, e, all_day = _parse_parts(s), _parse_parts(e), all_day == "true"
    if all_day:  # Calendar.app stores the all-day end as the following midnight
        last = (e - timedelta(days=1)).date()
        return {"uid": uid, "title": title, "start": s.date().isoformat(), "end": max(last, s.date()).isoformat(),
                "all_day": 1, "location": loc, "description": desc}
    return {"uid": uid, "title": title, "start": s.isoformat(timespec="minutes"),
            "end": e.isoformat(timespec="minutes"), "all_day": 0, "location": loc, "description": desc}


def _to_mac_args(f: dict) -> list[str]:
    c = _canon(f)
    if c["all_day"]:
        s = datetime.fromisoformat(c["start"])
        e = datetime.fromisoformat(c["end"]) + timedelta(days=1)
    else:
        s, e = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
    return [c["title"], *_parts(s), *_parts(e), "true" if c["all_day"] else "false", c["location"], c["description"]]


# ---------- Calendar.app operations ----------
def _read_mac() -> dict[str, dict]:
    now = datetime.now()
    a, b = now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
    out = _osa(_READ, str(a.year), str(a.month), str(a.day), str(b.year), str(b.month), str(b.day), calendar_name())
    res = {}
    for chunk in out.split(RS):
        rec = chunk.strip("\n").split(FS) if chunk.strip("\n") else None
        if rec and len(rec) == 7:
            ev = _from_mac(rec)
            res[ev["uid"]] = ev
    return res


def _write_mac(f: dict, uid: str = "") -> str:
    return _osa(_WRITE, calendar_name(), uid, *_to_mac_args(f))


def _in_window(start: str) -> bool:
    d = datetime.fromisoformat(start[:10])
    now = datetime.now()
    return now - timedelta(days=PAST_DAYS) <= d <= now + timedelta(days=FUTURE_DAYS)


# ---------- the sync pass ----------
def sync_once() -> dict:
    """One full two-way pass. Returns counts of what happened."""
    stats = {"pushed": 0, "pulled": 0, "created_mac": 0, "imported": 0, "deleted": 0}
    _osa(_ENSURE, calendar_name())
    mac = _read_mac()
    with db.connect() as conn:
        # Deletions made in multiapp since the last pass.
        for r in conn.execute("SELECT apple_uid FROM deleted_events").fetchall():
            _osa(_DELETE, calendar_name(), r["apple_uid"])
            mac.pop(r["apple_uid"], None)
            stats["deleted"] += 1
        conn.execute("DELETE FROM deleted_events")

        local = db.rows(conn.execute("SELECT * FROM events"))
        known = set()
        for ev in local:
            uid = ev["apple_uid"]
            if not uid:
                new_uid = _write_mac(ev)
                conn.execute("UPDATE events SET apple_uid=?, sync_hash=? WHERE id=?", (new_uid, _hash(ev), ev["id"]))
                known.add(new_uid)
                stats["created_mac"] += 1
                continue
            known.add(uid)
            m = mac.get(uid)
            if m is None:
                if _in_window(ev["start"]):  # gone from Calendar.app -> delete here too
                    conn.execute("DELETE FROM reminders WHERE event_id=?", (ev["id"],))
                    conn.execute("DELETE FROM events WHERE id=?", (ev["id"],))
                    stats["deleted"] += 1
                continue
            if _hash(ev) != ev["sync_hash"]:          # changed in multiapp (wins ties)
                _write_mac(ev, uid)
                conn.execute("UPDATE events SET sync_hash=? WHERE id=?", (_hash(ev), ev["id"]))
                stats["pushed"] += 1
            elif _hash(m) != ev["sync_hash"]:         # changed in Calendar.app
                _pull(conn, ev, m)
                stats["pulled"] += 1

        for uid, m in mac.items():                      # new in Calendar.app
            if uid in known:
                continue
            cur = conn.execute(
                "INSERT INTO events(title,start,end,all_day,location,description,remind_min,apple_uid,sync_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (m["title"], m["start"], m["end"], m["all_day"], m["location"], m["description"],
                 -1, uid, _hash(m)))  # remind_min -1: Calendar.app already alerts you on your phone
            stats["imported"] += 1
            del cur
    return stats


def _pull(conn, ev: dict, m: dict) -> None:
    conn.execute(
        "UPDATE events SET title=?,start=?,end=?,all_day=?,location=?,description=?,sync_hash=? WHERE id=?",
        (m["title"], m["start"], m["end"], m["all_day"], m["location"], m["description"], _hash(m), ev["id"]))
    after = dict(conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone())
    services._sync_event_reminder(conn, after, timing_changed=True)
