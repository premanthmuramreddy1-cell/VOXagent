"""Seeing and driving the Mac this server runs on, through the accessibility tree (no screenshots, no vision
model): the front window's controls become a numbered text list the agent's model can read, and each control is
pressed by its path in that tree. Only the screen_control skill uses this, and only for the owner (see there)."""

import asyncio
import json
import re

JXA_SEE = r"""
const se = Application("System Events");
const p = se.processes.whose({frontmost: true})[0];
const out = [];
function walk(el, path, depth) {
  if (out.length >= 120 || depth > 9) return;
  let kids = [];
  try { kids = el.uiElements(); } catch (e) { return; }
  for (let i = 0; i < kids.length && out.length < 120; i++) {
    const k = kids[i], here = path.concat([i]);
    let role = "", name = "";
    try { role = k.role(); } catch (e) {}
    try { name = k.name() || k.description() || ""; } catch (e) {}
    if (!name) { try { const v = k.value(); if (typeof v === "string") name = v.slice(0, 60); } catch (e) {} }
    if (name && /Button|Field|Link|MenuItem|CheckBox|RadioButton|PopUp|Tab|Cell|Row|StaticText/.test(role))
      out.push({role: role.replace("AX", ""), name: String(name).slice(0, 80), path: here});
    walk(k, here, depth + 1);
  }
}
let win = null;
try { win = p.windows[0]; win.name(); } catch (e) { win = null; }
if (win) walk(win, [], 0);
JSON.stringify({app: p.name(), window: win ? win.name() : "", items: out});
"""

JXA_PRESS = r"""
function run(argv) {
  const se = Application("System Events");
  let el = se.processes.whose({frontmost: true})[0].windows[0];
  for (const i of JSON.parse(argv[0])) el = el.uiElements()[i];
  try { el.actions["AXPress"].perform(); } catch (e) { el.focused = true; }
  return "ok";
}
"""

JXA_SECURE_FOCUS = r"""
const p = Application("System Events").processes.whose({frontmost: true})[0];
let sub = "";
try { sub = p.attributes["AXFocusedUIElement"].value().subrole(); } catch (e) {}
sub;
"""

KEYS = {"return": 36, "enter": 36, "tab": 48, "escape": 53, "esc": 53, "space": 49, "delete": 51,
        "up": 126, "down": 125, "left": 123, "right": 124}
MODS = {"cmd": "command down", "command": "command down", "shift": "shift down", "alt": "option down",
        "option": "option down", "ctrl": "control down", "control": "control down"}
APP_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,60}$")


async def _osa(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec("osascript", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 30)
    if proc.returncode:
        raise RuntimeError(err.decode().strip() or "osascript failed")
    return out.decode().strip()


async def see() -> dict:
    """{app, window, items: [{role, name, path}]} for the front window."""
    return json.loads(await _osa("-l", "JavaScript", "-e", JXA_SEE))


async def press(path: list[int]) -> None:
    await _osa("-l", "JavaScript", "-e", JXA_PRESS, json.dumps(path))


async def type_text(text: str) -> None:
    if await _osa("-l", "JavaScript", "-e", JXA_SECURE_FOCUS) == "AXSecureTextField":
        raise PermissionError("the focused field is a password field, which I never type into")
    await _osa("-e", "on run argv", "-e", 'tell application "System Events" to keystroke (item 1 of argv)',
               "-e", "end run", text)


async def key(combo: str) -> None:
    """'return', 'cmd+l', 'shift+tab' ..."""
    *mods, last = [part.strip().lower() for part in combo.split("+")]
    using = [MODS[m] for m in mods if m in MODS]
    tail = f" using {{{', '.join(using)}}}" if using else ""
    stroke = f"key code {KEYS[last]}" if last in KEYS else f'keystroke "{last[:1]}"' if last[:1].isalnum() else ""
    if not stroke:
        raise ValueError(f"unknown key {combo!r}")
    await _osa("-e", f'tell application "System Events" to {stroke}{tail}')


async def open_app(name: str) -> None:
    if not APP_NAME.match(name):
        raise ValueError(f"not an app name: {name!r}")
    proc = await asyncio.create_subprocess_exec("open", "-a", name, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(err.decode().strip() or f"could not open {name}")
    await asyncio.sleep(1.0)  # let the window appear before the next look
