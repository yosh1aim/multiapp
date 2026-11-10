import os
import subprocess
from pathlib import Path

from fastapi import APIRouter

from .. import notify

router = APIRouter(prefix="/api/settings", tags=["settings"])

PLIST = Path.home() / "Library/LaunchAgents/com.multiapp.server.plist"


def _launch_at_login() -> bool:
    if not PLIST.exists():
        return False
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.multiapp.server"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@router.get("")
def get_settings():
    return {
        "ntfy_server": notify.server(),
        "ntfy_topic": notify.topic(),
        "claude_configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "launch_at_login": _launch_at_login(),
    }


@router.post("/test-notification")
def test_notification():
    ok = notify.send("multiapp", "Test notification — phone alerts are working.", tags=["white_check_mark"])
    return {"ok": ok}
