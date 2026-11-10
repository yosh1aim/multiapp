"""Phone push notifications through ntfy (https://ntfy.sh)."""
import os
import secrets

import httpx

from . import db


def server() -> str:
    return os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def topic() -> str:
    """Topic from .env, else a stored random one (created on first use).

    The topic name is the only secret protecting your notifications on public ntfy.sh.
    """
    env = os.environ.get("NTFY_TOPIC", "").strip()
    if env:
        return env
    stored = db.get_setting("ntfy_topic")
    if stored:
        return stored
    new = "multiapp-" + secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")
    db.set_setting("ntfy_topic", new)
    return new


def send(title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> bool:
    """Returns True if ntfy accepted the message. Never raises."""
    headers = {"Title": title.encode("utf-8"), "Priority": str(priority)}
    if tags:
        headers["Tags"] = ",".join(tags)
    try:
        r = httpx.post(
            f"{server()}/{topic()}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        return r.is_success
    except httpx.HTTPError:
        return False
