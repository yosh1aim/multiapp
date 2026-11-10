import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "multiapp.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    start TEXT NOT NULL,          -- ISO local datetime, or YYYY-MM-DD when all_day
    end TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    remind_min INTEGER NOT NULL DEFAULT 15   -- minutes before start; -1 = no reminder
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,         -- ISO local datetime
    repeat TEXT NOT NULL DEFAULT 'none',
    done INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    event_id INTEGER                -- set when auto-created from a calendar event
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL DEFAULT '',           -- text notes: the note; checklists: the title
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',       -- 'text' | 'checklist'
    reset_mode TEXT NOT NULL DEFAULT 'manual',  -- checklists: 'manual' | 'daily'
    reset_time TEXT NOT NULL DEFAULT '06:00',   -- HH:MM for daily resets
    last_reset TEXT
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checklist_log (
    day TEXT NOT NULL,            -- YYYY-MM-DD the ticks belong to
    note_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, note_id, item_id)
);
CREATE TABLE IF NOT EXISTS date_notes (
    date TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Upgrade databases created before these columns existed.
        _ensure_column(conn, "events", "remind_min", "INTEGER NOT NULL DEFAULT 15")
        _ensure_column(conn, "reminders", "event_id", "INTEGER")
        _ensure_column(conn, "notes", "kind", "TEXT NOT NULL DEFAULT 'text'")
        _ensure_column(conn, "notes", "reset_mode", "TEXT NOT NULL DEFAULT 'manual'")
        _ensure_column(conn, "notes", "reset_time", "TEXT NOT NULL DEFAULT '06:00'")
        _ensure_column(conn, "notes", "last_reset", "TEXT")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
