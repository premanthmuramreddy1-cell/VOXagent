"""System control skill for macOS automation (apps, audio, media, display, and power)."""

import asyncio
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.skills import Permission, RunContext, SkillResult

SAFE_APP_NAME = re.compile(r"^[A-Za-z0-9 _\-.]+$")

ActionType = Literal[
    "volume",
    "media",
    "app_open",
    "app_quit",
    "open_url",
    "battery",
    "lock_screen",
    "open_folder",
]


class Args(BaseModel):
    action: ActionType = Field(description="The system control action to perform.")
    value: str = Field(
        default="",
        description="Target value: volume number (0-100) or 'mute'/'unmute'; media command ('play', 'pause', 'next', 'prev'); app name ('Spotify', 'Safari'); URL; or folder path.",
    )


async def _run_cmd(*cmd: str) -> tuple[int, str, str]:
    """Execute a system binary safely with explicit argument vector (no shell=True)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode().strip(), stderr.decode().strip()


async def handle_volume(val: str) -> tuple[str, dict[str, Any]]:
    clean = val.strip().lower()
    if clean == "mute":
        await _run_cmd("osascript", "-e", "set volume output muted true")
        return "Audio muted.", {"muted": True}
    if clean == "unmute":
        await _run_cmd("osascript", "-e", "set volume output muted false")
        return "Audio unmuted.", {"muted": False}
    if not clean:
        _, out, _ = await _run_cmd("osascript", "-e", "output volume of (get volume settings)")
        return f"Current output volume is {out}%.", {"volume": out}

    m = re.search(r"\d+", clean)
    if not m:
        raise ValueError("specify volume between 0 and 100, or 'mute'/'unmute'")
    level = max(0, min(100, int(m.group(0))))
    await _run_cmd("osascript", "-e", f"set volume output volume {level}")
    return f"Output volume set to {level}%.", {"volume": level}


async def handle_media(cmd: str) -> tuple[str, dict[str, Any]]:
    clean = cmd.strip().lower()
    script = ""
    if clean in ("play", "pause", "playpause", "toggle"):
        script = 'tell application "System Events" to key code 16 using {}'  # Play/Pause media key fallback
        summary = "Toggled media playback."
    elif clean in ("next", "skip"):
        summary = "Skipped to next media track."
        script = 'tell application "System Events" to key code 19 using {}'
    elif clean in ("prev", "previous", "back"):
        summary = "Returned to previous media track."
        script = 'tell application "System Events" to key code 20 using {}'
    else:
        raise ValueError("media command must be one of: play, pause, next, previous")

    await _run_cmd("osascript", "-e", script)
    return summary, {"command": clean}


async def handle_app_open(app_name: str) -> tuple[str, dict[str, Any]]:
    name = app_name.strip()
    if not name or not SAFE_APP_NAME.match(name):
        raise ValueError("invalid application name")
    code, _, err = await _run_cmd("open", "-a", name)
    if code != 0:
        raise ValueError(f"could not open application '{name}': {err or 'app not found'}")
    return f"Opened application {name}.", {"app": name}


async def handle_app_quit(app_name: str) -> tuple[str, dict[str, Any]]:
    name = app_name.strip()
    if not name or not SAFE_APP_NAME.match(name):
        raise ValueError("invalid application name")
    # Escape quotes for AppleScript string literal
    safe_name = name.replace('"', '\\"')
    script = f'tell application "{safe_name}" to quit'
    await _run_cmd("osascript", "-e", script)
    return f"Closed application {name}.", {"app": name}


async def handle_open_url(url: str) -> tuple[str, dict[str, Any]]:
    clean = url.strip()
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https URLs can be opened")
    await _run_cmd("open", clean)
    return f"Opened {clean} in default browser.", {"url": clean}


async def handle_battery() -> tuple[str, dict[str, Any]]:
    code, out, _ = await _run_cmd("pmset", "-g", "batt")
    if code != 0 or not out:
        return "Battery status unavailable.", {"battery": "unknown"}
    percent_match = re.search(r"(\d+)%", out)
    state_match = re.search(r";\s*([a-zA-Z\s]+);", out)
    pct = percent_match.group(1) if percent_match else "unknown"
    state = state_match.group(1).strip() if state_match else "normal"
    return f"Battery is at {pct}% ({state}).", {"percentage": pct, "state": state}


async def handle_lock() -> tuple[str, dict[str, Any]]:
    await _run_cmd("pmset", "displaysleepnow")
    return "Screen locked.", {"locked": True}


async def handle_folder(path: str) -> tuple[str, dict[str, Any]]:
    clean = path.strip() or "~"
    await _run_cmd("open", clean)
    return f"Opened folder {clean} in Finder.", {"path": clean}


class SystemControl:
    name = "system_control"
    description = (
        "Control your laptop: adjust audio volume (0-100 or mute/unmute), control media playback (play/pause/next), "
        "launch or close applications, open web URLs or folders in Finder, check battery percentage, or lock screen. "
        "Use when the user asks to control system hardware, launch software, or check power/audio."
    )
    input_schema = Args
    permission = Permission.CHANGE

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        act = args.action
        val = args.value

        if act == "volume":
            summary, data = await handle_volume(val)
        elif act == "media":
            summary, data = await handle_media(val)
        elif act == "app_open":
            summary, data = await handle_app_open(val)
        elif act == "app_quit":
            summary, data = await handle_app_quit(val)
        elif act == "open_url":
            summary, data = await handle_open_url(val)
        elif act == "battery":
            summary, data = await handle_battery()
        elif act == "lock_screen":
            summary, data = await handle_lock()
        elif act == "open_folder":
            summary, data = await handle_folder(val)
        else:
            raise ValueError(f"unsupported action: {act}")

        return SkillResult(summary, {"action": act, **data})


skill = SystemControl()
