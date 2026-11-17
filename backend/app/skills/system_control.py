"""System control skill for macOS automation (apps, audio, media, display, and power)."""

import asyncio
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.skills import Permission, RunContext, SkillResult, is_owner

SAFE_APP_NAME = re.compile(r"^[A-Za-z0-9 _\-.]+$")
# macOS media keys: 16 play/pause, 17 next, 18 previous.
MEDIA_KEYS = {"playpause": 16, "next track": 17, "previous track": 18}

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


async def _run_checked(action_label: str, *cmd: str) -> str:
    """Run a system binary and raise a ValueError if it exits non-zero."""
    code, out, err = await _run_cmd(*cmd)
    if code != 0:
        detail = err or out or f"exit code {code}"
        raise ValueError(f"could not {action_label}: {detail}")
    return out


async def handle_volume(val: str) -> tuple[str, dict[str, Any]]:
    clean = val.strip().lower()
    if clean == "mute":
        await _run_checked("mute audio", "osascript", "-e", "set volume output muted true")
        return "Audio muted.", {"muted": True}
    if clean == "unmute":
        await _run_checked("unmute audio", "osascript", "-e", "set volume output muted false")
        return "Audio unmuted.", {"muted": False}
    if not clean:
        out = await _run_checked("get volume settings", "osascript", "-e", "output volume of (get volume settings)")
        return f"Current output volume is {out}%.", {"volume": out}

    m = re.search(r"\d+", clean)
    if not m:
        raise ValueError("specify volume between 0 and 100, or 'mute'/'unmute'")
    level = max(0, min(100, int(m.group(0))))
    await _run_checked(f"set volume to {level}%", "osascript", "-e", f"set volume output volume {level}")
    return f"Output volume set to {level}%.", {"volume": level}


async def handle_media(cmd: str) -> tuple[str, dict[str, Any]]:
    clean = cmd.strip().lower()
    if clean in ("play", "pause", "playpause", "toggle"):
        apple_cmd = "playpause"
        summary = "Toggled media playback."
    elif clean in ("next", "skip"):
        apple_cmd = "next track"
        summary = "Skipped to next media track."
    elif clean in ("prev", "previous", "back"):
        apple_cmd = "previous track"
        summary = "Returned to previous media track."
    else:
        raise ValueError("media command must be one of: play, pause, next, previous")

    script = f"""
    tell application "System Events"
        set runningApps to name of every process
    end tell
    if runningApps contains "Spotify" then
        tell application "Spotify" to {apple_cmd}
        return "Spotify"
    else if runningApps contains "Music" then
        tell application "Music" to {apple_cmd}
        return "Music"
    else
        tell application "System Events" to key code {MEDIA_KEYS[apple_cmd]}
        return "system media key"
    end if
    """
    player = await _run_checked("control media playback", "osascript", "-e", script.strip())
    return summary, {"command": clean, "player": player}


async def handle_app_open(app_name: str) -> tuple[str, dict[str, Any]]:
    name = app_name.strip()
    if not name or name.startswith("-") or not SAFE_APP_NAME.match(name):
        raise ValueError("invalid application name")
    await _run_checked(f"open application '{name}'", "open", "-a", name)
    return f"Opened application {name}.", {"app": name}


async def handle_app_quit(app_name: str) -> tuple[str, dict[str, Any]]:
    name = app_name.strip()
    if not name or name.startswith("-") or not SAFE_APP_NAME.match(name):
        raise ValueError("invalid application name")
    safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "{safe_name}" to quit'
    await _run_checked(f"quit application '{name}'", "osascript", "-e", script)
    return f"Closed application {name}.", {"app": name}


async def handle_open_url(url: str) -> tuple[str, dict[str, Any]]:
    clean = url.strip()
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("only valid http and https URLs can be opened")
    await _run_checked(f"open URL '{clean}'", "open", "--", clean)
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
    lock_script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
    code, _, _ = await _run_cmd("osascript", "-e", lock_script)
    if code != 0:
        await _run_checked("lock screen", "pmset", "displaysleepnow")
    return "Screen locked.", {"locked": True}


async def handle_folder(path: str) -> tuple[str, dict[str, Any]]:
    raw = path.strip() or "~"
    if raw.startswith("-"):
        raise ValueError("invalid folder path")
    target = Path(raw).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise ValueError(f"folder does not exist or is not a directory: {raw}")
    if target.suffix.lower() in (".app", ".pkg", ".command", ".sh"):
        raise ValueError("cannot open application bundles or scripts as folders")
    await _run_checked(f"open folder '{target}'", "open", "--", str(target))
    return f"Opened folder {target} in Finder.", {"path": str(target)}


class SystemControl:
    name = "system_control"
    owner_only = True
    description = (
        "Control this computer: set or mute the volume, play, pause or skip music, open or quit any app "
        "('open Spotify', 'quit Safari'), open a website in the browser ('open youtube.com'), open a folder in "
        "Finder, check the battery ('how much battery do I have'), or lock the screen. "
        "Use when the user asks to control system hardware, launch software, or check power/audio."
    )
    input_schema = Args
    permission = Permission.CHANGE

    async def run(self, args: Args, ctx: RunContext) -> SkillResult:
        if not is_owner(ctx.user_id):  # the apps, volume and files are the owner's, whoever is talking
            return SkillResult("Only the owner of this computer can control its apps, sound and files.", {"ok": False})
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
