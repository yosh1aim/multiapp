"""Claude chat: streaming responses plus tools that act on the calendar/reminders/notes."""
import json
import os
from datetime import datetime
from typing import AsyncIterator

import anthropic

from . import db, services

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 8
HISTORY_LIMIT = 40  # past messages sent back to Claude

SYSTEM = """You are the assistant inside multiapp, Kacper's personal planner (calendar, reminders, date notes).

Use your tools to act on the planner when asked: create reminders and events, delete events, add notes to dates, list what is coming up, mark reminders done. After acting, confirm briefly in plain words. If a request is ambiguous about the date or time, ask one short question instead of guessing. Reminders are sent to the user's phone at their due time. Every event you create automatically gets a reminder 15 minutes before it starts, so don't create a separate reminder for the same event.

Each user message starts with a line giving the current local date, time and weekday - use it to resolve phrases like "tomorrow" or "Thursday at 3". Datetimes you pass to tools are local time in the form YYYY-MM-DDTHH:MM. Keep replies short and friendly; use markdown only when it helps."""

_BASE = {"type": "object", "additionalProperties": False}

TOOLS = [
    {
        "name": "create_reminder",
        "description": "Create a reminder that is pushed to the user's phone at its due time.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["text", "due_at", "repeat"], "properties": {
            "text": {"type": "string", "description": "What to be reminded of."},
            "due_at": {"type": "string", "description": "Local datetime, YYYY-MM-DDTHH:MM."},
            "repeat": {"type": "string", "enum": ["none", "daily", "weekly"]},
        }},
    },
    {
        "name": "create_event",
        "description": "Add an event to the calendar.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["title", "start", "end", "all_day", "location"], "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, or YYYY-MM-DD if all_day."},
            "end": {"type": "string", "description": "Same format as start; empty string if none."},
            "all_day": {"type": "boolean"},
            "location": {"type": "string", "description": "Empty string if none."},
        }},
    },
    {
        "name": "delete_event",
        "description": "Delete a calendar event by id (get ids from list_upcoming). Also removes its auto-created reminder. Only do this when the user clearly asked to remove that event.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["id"], "properties": {"id": {"type": "integer"}}},
    },
    {
        "name": "set_date_note",
        "description": "Write or append a note/comment shown beside a calendar date.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["date", "body", "append"], "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "body": {"type": "string"},
            "append": {"type": "boolean", "description": "True to add to the existing note, false to replace it."},
        }},
    },
    {
        "name": "list_upcoming",
        "description": "List upcoming events and pending reminders (with ids) for the next N days.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["days"], "properties": {
            "days": {"type": "integer", "description": "How many days ahead, 1-90."},
        }},
    },
    {
        "name": "complete_reminder",
        "description": "Mark a reminder done by id (get ids from list_upcoming).",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {**_BASE, "required": ["id"], "properties": {"id": {"type": "integer"}}},
    },
]


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def run_tool(name: str, args: dict) -> tuple[str, str]:
    """Execute a tool. Returns (result_text_for_claude, short_label_for_the_UI)."""
    if name == "create_reminder":
        r = services.create_reminder(args["text"], args["due_at"], args.get("repeat", "none"))
        return json.dumps(r), f"Reminder set: {r['text']} ({r['due_at'][:16].replace('T', ' ')})"
    if name == "create_event":
        e = services.create_event(args["title"], args["start"], args.get("end") or None,
                                  bool(args.get("all_day")), args.get("location", ""))
        return json.dumps(e), f"Event added: {e['title']} ({e['start'][:16].replace('T', ' ')})"
    if name == "delete_event":
        eid = int(args["id"])
        title = next((e["title"] for e in services.list_events() if e["id"] == eid), None)
        if title is None or not services.delete_event(eid):
            raise ValueError(f"No event with id {eid}")
        return json.dumps({"deleted": eid}), f"Event removed: {title}"
    if name == "set_date_note":
        n = services.set_note(args["date"], args["body"], bool(args.get("append")))
        return json.dumps(n), f"Note saved for {n['date']}"
    if name == "list_upcoming":
        days = max(1, min(int(args["days"]), 90))
        return json.dumps(services.upcoming(days)), f"Checked the next {days} days"
    if name == "complete_reminder":
        r = services.complete_reminder(int(args["id"]))
        if not r:
            raise ValueError(f"No reminder with id {args['id']}")
        return json.dumps(r), f"Reminder completed: {r['text']}"
    raise ValueError(f"Unknown tool {name}")


def _load_history() -> list[dict]:
    with db.connect() as conn:
        rows = db.rows(conn.execute(
            "SELECT role, content_json FROM chat_messages ORDER BY id DESC LIMIT ?", (HISTORY_LIMIT,)))
    msgs = [{"role": r["role"], "content": json.loads(r["content_json"])} for r in reversed(rows)]
    while msgs and msgs[0]["role"] != "user":  # history must start with a user turn
        msgs.pop(0)
    return msgs


def _save(role: str, text: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO chat_messages(role,content_json,created_at) VALUES(?,?,?)",
                     (role, json.dumps(text), db.now_iso()))


def history_for_ui() -> list[dict]:
    return [{"role": m["role"], "text": m["content"]} for m in _load_history()]


def clear_history() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM chat_messages")


def _event(kind: str, **data) -> str:
    return f"data: {json.dumps({'type': kind, **data})}\n\n"


async def stream_chat(user_text: str) -> AsyncIterator[str]:
    """Yields SSE frames: text deltas, tool chips, errors, and a final done."""
    if not configured():
        yield _event("error", code="no_key", message="Add your Anthropic API key to ~/multiapp/.env and restart.")
        return

    stamp = datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M (%Z)")
    history = _load_history()
    messages = history + [{"role": "user", "content": f"[Now: {stamp}]\n{user_text}"}]
    _save("user", user_text)

    client = anthropic.AsyncAnthropic()
    final_text = ""
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            async with client.beta.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                messages=messages,
                tools=TOOLS,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        final_text += event.delta.text
                        yield _event("text", text=event.delta.text)
                msg = await stream.get_final_message()

            if msg.stop_reason == "refusal":
                yield _event("error", code="refusal", message="Claude declined to answer that request.")
                break
            if msg.stop_reason == "max_tokens":
                yield _event("error", code="max_tokens", message="The reply was cut off. Try a shorter request.")
                break
            if msg.stop_reason != "tool_use":
                break

            # Keep the full assistant content (incl. thinking blocks) for the tool round-trip.
            messages.append({"role": "assistant", "content": msg.content})
            results = []
            for block in msg.content:
                if block.type != "tool_use":
                    continue
                try:
                    out, label = run_tool(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                    yield _event("tool", ok=True, label=label, name=block.name)
                except (ValueError, KeyError, TypeError) as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {e}", "is_error": True})
                    yield _event("tool", ok=False, label=f"Couldn't run {block.name}: {e}", name=block.name)
            messages.append({"role": "user", "content": results})
            if final_text and not final_text.endswith("\n"):
                final_text += "\n\n"
                yield _event("text", text="\n\n")
        if final_text.strip():
            _save("assistant", final_text.strip())
    except anthropic.AuthenticationError:
        yield _event("error", code="bad_key", message="Your Anthropic API key was rejected. Check ~/multiapp/.env.")
    except anthropic.RateLimitError:
        yield _event("error", code="rate_limit", message="Rate limited by the API. Try again in a minute.")
    except anthropic.APIConnectionError:
        yield _event("error", code="offline", message="Couldn't reach the Claude API. Check your internet connection.")
    except anthropic.APIStatusError as e:
        yield _event("error", code="api", message=f"Claude API error ({e.status_code}): {e.message}")
    yield _event("done")
