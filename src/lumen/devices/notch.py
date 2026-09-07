"""A notch-style status tab at the top of the screen — the "notch" every machine has.

A dark tab hangs from the top edge (on a MacBook, from the menu bar, right
under the hardware notch). It is split into zones like a keyboard, so the
`sessions` effect paints one soft-lit bar per open agent tab in its status
colour: a glance at the top edge says which tabs are working and which are
waiting on you. Hovering it unfolds a list of the live sessions — agent,
folder, state. All zones black hides the tab.

Same shape as the screen glow: a tkinter child process fed over stdin, one
line per update — "r g b" for a single colour or "r g b r g b ..." per zone,
optionally followed by " | " and JSON {"sessions": [...], "usage": {...}} (the
device sits in the daemon, so it can hand the child what the keyboard cannot
show: which tab is which, how full its context window is, and how much of the
5-hour and 7-day Claude limits are gone). The picture itself
is drawn by Pillow (supersampled, so edges and text are smooth) and handed to
Tk as a PNG. Settings → "Status tab at the top of the screen" turns it off,
picks its corner (top centre, top left/right, bottom centre) and whether it
hides while a game or video runs full screen. Clicking a row brings that
tab's terminal to the front.

    python -m lumen.devices.notch     # the child (source install)
    lumen notch-child                 # the child (frozen build; same code)
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

from lumen import paths
from lumen.core.devices import COLOR, RGB, ZONES
from lumen.devices.screen import ScreenGlow

ZONE_COUNT = 6
# Geometry (logical px). The tab is a flat-topped capsule; hover widens it into a panel.
WIDTH, HEIGHT, RADIUS = 236, 24, 12
PANEL_WIDTH, ROW, PANEL_PAD = 372, 30, 12
USAGE_W = 58                 # room at the right of the folded tab for "5h 23%"
METER_H = 4
BAR_H, BAR_GAP, BAR_INSET = 6, 6, 20
ALPHA = 0.96
SHELL = (12, 13, 17)
EDGE = (255, 255, 255, 22)   # 1px inner hairline
INK, MUTED = (236, 237, 241), (140, 143, 154)
KEY = (1, 0, 1)              # Windows chroma key; on macOS the window is truly transparent
LABELS = {"running": "working", "input": "needs you", "done": "done"}
SS = 3                       # supersample factor for Pillow drawing
POSITIONS = ("top", "top-left", "top-right", "bottom")
# Everything the tab can show, all on by default. Settings turns pieces off:
# someone who only wants the Claude limits and the live tabs unticks the rest.
SHOW_DEFAULTS = {"sessions": True, "context": True, "cost": True, "activity": True,
                 "claude_usage": True, "codex_usage": True}
PULSE_S = 1.6                # a tab that just started waiting on you breathes this long


def _get_lanczos_filter() -> int:
    """Get the Pillow resampling filter constant, handling old and new versions."""
    try:
        from PIL import Image
        return Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except AttributeError:
        from PIL import Image
        return Image.LANCZOS  # type: ignore[attr-defined]


LANCZOS_FILTER = _get_lanczos_filter()


def child_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "notch-child"]
    return [sys.executable, "-m", "lumen.devices.notch"]


class Notch(ScreenGlow):
    def __init__(self):
        super().__init__()
        self.id, self.name, self.vendor = "notch", "Notch status tab", ""
        self.capabilities = frozenset({COLOR, ZONES})
        self.zone_count = ZONE_COUNT
        self.ambient = True  # its whole purpose is the persistent sessions view
        self.details = {"connection": "built-in", "zones": ZONE_COUNT}
        self.position, self.hide_fullscreen = "top", True
        self.show = dict(SHOW_DEFAULTS)   # what the tab displays; Settings can trim it to "just my limits"
        self.agents = "all"               # whose sessions: all | claude | codex

    def child_command(self) -> list[str]:
        return child_command()

    def set_color(self, rgb) -> None:
        self.set_zones([tuple(rgb)] * ZONE_COUNT)

    def set_zones(self, colors: list[RGB]) -> None:
        from lumen.integrations import claude_usage, codex_usage
        from lumen.integrations.agent_sessions import all_sessions
        payload = {"sessions": all_sessions(),
                   "usage": {"claude": claude_usage.latest(), "codex": codex_usage.latest()},
                   "options": {"position": self.position, "hide_fullscreen": self.hide_fullscreen,
                               "show": self.show, "agents": self.agents}}
        self._send(" ".join("%d %d %d" % tuple(c) for c in colors) + " | " + json.dumps(payload))


_instance: Notch | None = None


def discover(settings: dict | None = None) -> list:
    global _instance
    if not (settings or {}).get("notch", True):
        if _instance is not None:
            _instance.shutdown()  # switched off in Settings: take the tab down now, not at the next effect
        return []
    try:
        import tkinter  # noqa: F401

        import PIL  # noqa: F401
    except ImportError:
        return []
    if _instance is None:
        _instance = Notch()
    position = str((settings or {}).get("notch_position") or "top")
    _instance.position = position if position in POSITIONS else "top"
    _instance.hide_fullscreen = bool((settings or {}).get("notch_hide_fullscreen", True))
    _instance.show = {k: bool((settings or {}).get(f"notch_show_{k}", v)) for k, v in SHOW_DEFAULTS.items()}
    agents = str((settings or {}).get("notch_agents") or "all")
    _instance.agents = agents if agents in ("all", "claude", "codex") else "all"
    _instance.connected = True
    _instance.warm_up()
    return [_instance]


# ---------------------------------------------------------------------------
# Child process: pure functions first (tested), then the Tk loop
# ---------------------------------------------------------------------------

def parse_line(line: str, n: int = ZONE_COUNT) -> tuple[list[RGB], list[dict], dict, dict] | None:
    """One stdin line: zones, the session list, the usage summary and the display options (empty if absent)."""
    colours, _, extra = line.partition("|")
    zones = parse_zones(colours, n)
    if zones is None:
        return None
    try:
        payload = json.loads(extra) if extra.strip() else {}
    except ValueError:
        payload = {}
    if isinstance(payload, list):  # an older daemon: the bare session list
        payload = {"sessions": payload}
    if not isinstance(payload, dict):
        payload = {}
    sessions = payload.get("sessions") or []
    usage = payload.get("usage") or {}
    options = payload.get("options") or {}
    return (zones, [s for s in sessions if isinstance(s, dict)] if isinstance(sessions, list) else [],
            clean_usage(usage), options if isinstance(options, dict) else {})


def clean_usage(usage) -> dict:
    """Usage as {agent: summary}. A bare summary (the 0.5 daemon) is Claude's;
    blocks without a numeric "used" are dropped."""
    if not isinstance(usage, dict):
        return {}
    if "five_hour" in usage or "seven_day" in usage:
        usage = {"claude": usage}
    out = {}
    for agent, summary in usage.items():
        if not isinstance(summary, dict):
            continue
        blocks = {k: v for k, v in summary.items() if isinstance(v, dict) and isinstance(v.get("used"), (int, float))}
        if blocks:
            out[str(agent)] = blocks
    return out


def apply_show(rows: list[tuple], show: dict, agents: str = "all") -> list[tuple]:
    """Trim the session rows to what Settings asks for: only one agent's tabs,
    and without context / cost / activity when those are switched off."""
    keep = lambda k: show.get(k, True)
    out = []
    for agent, name, status, pct, activity, cost in rows:
        if agents != "all" and agent != agents:
            continue
        out.append((agent, name, status, pct if keep("context") else None,
                    activity if keep("activity") else "", cost if keep("cost") else None))
    return out


def visible_usage(usage: dict, sessions: list[dict], show: dict, agents: str = "all") -> dict:
    """Which limit meters belong on the tab right now.

    A meter follows its agent's tabs. The limit itself is account-wide and stays
    true whether or not anything is open — but the tab is a picture of what is
    happening now, and a Codex meter still sitting there hours after Codex was
    closed reads as stale data, not as information. Close Codex and its meter
    goes; open it again and it comes back. Settings can switch either meter off,
    and pointing the tab at one agent hides the other's meter as well as its
    rows."""
    live = {str(s.get("agent") or "") for s in sessions if isinstance(s, dict)}
    return {a: s for a, s in usage.items()
            if a in live and show.get(f"{a}_usage", True) and (agents == "all" or a == agents)}


def parse_zones(line: str, n: int = ZONE_COUNT) -> list[RGB] | None:
    """"r g b [r g b ...]" as exactly n zones (padded with the last colour), or None."""
    try:
        vals = [max(0, min(255, int(v))) for v in line.split()]
    except ValueError:
        return None
    if len(vals) < 3:
        return None
    zones = [cast(RGB, tuple(vals[i:i + 3])) for i in range(0, len(vals) - len(vals) % 3, 3)]
    return (zones + [zones[-1]] * n)[:n]


def runs(zones: list[RGB]) -> list[tuple[RGB, int]]:
    """Adjacent zones of one colour are one tab: [(colour, width_in_zones), ...], black skipped."""
    out: list[tuple[RGB, int]] = []
    for z in zones:
        if out and out[-1][0] == z:
            out[-1] = (z, out[-1][1] + 1)
        else:
            out.append((z, 1))
    return [(c, w) for c, w in out if c != (0, 0, 0)]


def session_rows(sessions: list[dict]) -> list[tuple[str, str, str, int | None, str, float | None]]:
    """(agent, name, status, context %, activity, cost) per live session in zone
    order — the daemon's snapshot, so the rows match the bars and a tab's label
    is its name. `activity` ("Editing api.py") and `cost` (USD) are optional
    snapshot fields; "" / None when the daemon doesn't know them."""
    from lumen.integrations.agent_sessions import context_percent
    rows = sorted(sessions, key=lambda s: int(s.get("slot", 0)))
    out = []
    for s in rows:
        cost = s.get("cost_usd")
        out.append((str(s.get("agent", "agent")),
                    str(s.get("label") or paths.basename(str(s.get("cwd") or "")) or "~"),
                    str(s.get("status")), context_percent(s), str(s.get("activity") or ""),
                    float(cost) if isinstance(cost, (int, float)) else None))
    return out


def panel_row_at(y: int, n_rows: int) -> int | None:
    """Which session row a click at image y (logical px) landed on, or None."""
    top = HEIGHT + PANEL_PAD
    if y < top:
        return None
    i = (y - top) // ROW
    return int(i) if 0 <= i < n_rows else None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = {
        "win32": [("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf")],
        "darwin": ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                   "/System/Library/Fonts/Supplemental/Arial.ttf"],
    }.get(sys.platform, ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        try:
            f = ImageFont.truetype(path, size)
            if bold and sys.platform == "darwin":
                try:
                    f.set_variation_by_name("Semibold")
                except Exception:
                    pass
            return f
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def usage_colour(pct: int) -> RGB:
    """Calm until it matters: neutral, amber past 70 %, red past 90 %."""
    return (230, 70, 70) if pct >= 90 else (240, 170, 40) if pct >= 70 else (150, 154, 168)


def render(zones: list[RGB], rows: list[tuple], palette: dict[str, RGB],
           opaque_key: RGB | None = None, fills: list[int | None] | None = None, usage: dict | None = None,
           unfolded: bool = False, now: float | None = None, flip: bool = False, glow_gain: float = 1.0):
    """The tab as an RGBA Pillow image (composited onto `opaque_key` where the
    platform can't do per-pixel alpha). `unfolded` = the hover panel: session
    rows, then the usage meters. `fills` is one context-window percentage per
    bar (None = solid bar); `usage` the 5-hour / 7-day summary, if known.
    `flip` rounds the top instead (the tab hangs up from the bottom edge);
    `glow_gain` > 1 brightens the bar glow for the "needs you" pulse."""
    from PIL import Image, ImageDraw, ImageFilter

    from lumen.integrations.claude_usage import resets_in

    # usage is one agent's summary, or {agent: summary} for several; the first
    # agent with a 5-hour figure owns the number on the folded tab
    by_agent = clean_usage(usage or {})
    meters = []
    if unfolded:
        for agent, summary in by_agent.items():
            who = agent.capitalize() if len(by_agent) > 1 else "Session"
            for k, name in (("five_hour", f"{who} · 5 hours"), ("seven_day", f"{who} · 7 days" if len(by_agent) > 1 else "Week · 7 days")):
                if k in summary:
                    meters.append((name, summary[k]))
    five = next((s["five_hour"]["used"] for s in by_agent.values() if "five_hour" in s), None)
    w = PANEL_WIDTH if unfolded else WIDTH
    h = HEIGHT
    if unfolded:
        h += PANEL_PAD + ROW * len(rows) + (PANEL_PAD // 2 + ROW * len(meters) if meters else 0) + PANEL_PAD - 6
    W, H, R = w * SS, h * SS, RADIUS * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # shell: flat on the screen-edge side, rounded on the other — it hangs from the edge
    if flip:
        d.rounded_rectangle((0, 0, W - 1, H - 1 + R), radius=R, fill=SHELL + (255,))
        d.rounded_rectangle((SS, SS, W - 1 - SS, H - 1 + R), radius=R - SS, outline=EDGE, width=SS)
    else:
        d.rounded_rectangle((0, -R, W - 1, H - 1), radius=R, fill=SHELL + (255,))
        d.rounded_rectangle((SS, -R, W - 1 - SS, H - 1 - SS), radius=R - SS, outline=EDGE, width=SS)

    # session bars, each lit from beneath
    bars = runs(zones)
    if bars:
        inner = W - 2 * BAR_INSET * SS - (USAGE_W * SS if five is not None and not unfolded else 0)
        total = sum(n for _, n in bars)
        gap = BAR_GAP * SS if len(bars) > 1 else 0
        unit = (inner - gap * (len(bars) - 1)) / total
        y0 = (HEIGHT * SS - BAR_H * SS) // 2
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        x = BAR_INSET * SS
        for colour, n in bars:
            x1 = x + unit * n
            gd.rounded_rectangle((x - 2 * SS, y0 - 2 * SS, x1 + 2 * SS, y0 + (BAR_H + 2) * SS), radius=BAR_H * SS,
                                 fill=colour + (int(min(255, 150 * glow_gain)),))
            x = x1 + gap
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4 * SS)))
        d = ImageDraw.Draw(img)
        x = BAR_INSET * SS
        for i, (colour, n) in enumerate(bars):
            x1 = x + unit * n
            pct = fills[i] if fills and i < len(fills) else None
            r = BAR_H * SS // 2
            if pct is None:
                d.rounded_rectangle((x, y0, x1, y0 + BAR_H * SS), radius=r, fill=colour + (255,))
            else:  # a dim track, filled to how much of the context window the tab has used
                d.rounded_rectangle((x, y0, x1, y0 + BAR_H * SS), radius=r, fill=colour + (70,))
                fill_w = max(BAR_H * SS, (x1 - x) * max(0, min(100, pct)) / 100)
                d.rounded_rectangle((x, y0, x + fill_w, y0 + BAR_H * SS), radius=r, fill=colour + (255,))
            x = x1 + gap

    if five is not None and not unfolded:  # "5h 23%" at the right of the folded tab
        f = _font(11 * SS, bold=True)
        colour = usage_colour(int(five))
        d.text((W - BAR_INSET * SS, HEIGHT * SS // 2), f"{int(five)}%", font=f, fill=colour + (255,), anchor="rm")
        px = d.textlength(f"{int(five)}%", font=f)
        d.text((W - BAR_INSET * SS - px - 5 * SS, HEIGHT * SS // 2), "5h", font=_font(10 * SS),
               fill=MUTED + (255,), anchor="rm")

    if unfolded:
        name_f, meta_f = _font(12 * SS, bold=True), _font(12 * SS)
        d.line((BAR_INSET * SS, HEIGHT * SS + 2 * SS, W - BAR_INSET * SS, HEIGHT * SS + 2 * SS), fill=EDGE, width=SS)
        y = (HEIGHT + PANEL_PAD) * SS
        if not rows and not meters:
            d.text((W // 2, y + ROW * SS // 2), "No open agent tabs", font=meta_f, fill=MUTED + (255,), anchor="mm")
        for row in rows:
            agent, folder, status, pct = row[:4]
            activity = row[4] if len(row) > 4 else ""
            cost = row[5] if len(row) > 5 else None
            colour = tuple(palette.get(status, MUTED[:3]))
            cy = y + ROW * SS // 2
            d.ellipse((BAR_INSET * SS, cy - 4 * SS, BAR_INSET * SS + 8 * SS, cy + 4 * SS), fill=colour + (255,))
            d.text((BAR_INSET * SS + 18 * SS, cy), agent.capitalize(), font=name_f, fill=INK + (255,), anchor="lm")
            nx = d.textlength(agent.capitalize(), font=name_f)
            label = cast(str, LABELS.get(status, status))
            d.text((W - BAR_INSET * SS, cy), label, font=meta_f, fill=colour + (255,), anchor="rm")
            right = W - BAR_INSET * SS - d.textlength(label, font=meta_f) - 12 * SS
            meta = "  ·  ".join(x for x in ((f"${cost:.2f}" if cost else ""), (f"{pct}%" if pct is not None else "")) if x)
            if meta:  # cost and context window, e.g. "$1.20  ·  63%", left of the state
                d.text((right, cy), meta, font=meta_f, fill=MUTED + (255,), anchor="rm")
                right -= d.textlength(meta, font=meta_f) + 12 * SS
            # name, then what the tab is doing right now; clipped to the room that is left
            text = folder + (f"  ·  {activity}" if activity and status == "running" else "")
            x0 = BAR_INSET * SS + 18 * SS + nx + 10 * SS
            while text and d.textlength(text, font=meta_f) > right - x0:
                text = text[:-2].rstrip() + "…" if len(text) > 2 else ""
            d.text((x0, cy), text, font=meta_f, fill=MUTED + (255,), anchor="lm")
            y += ROW * SS
        if meters:  # the subscription limits: a label, a thin meter, the number and the reset time
            if rows:
                d.line((BAR_INSET * SS, y + 2 * SS, W - BAR_INSET * SS, y + 2 * SS), fill=EDGE, width=SS)
                y += PANEL_PAD * SS // 2
            small = _font(11 * SS)
            for name, block in meters:
                pct = int(block["used"])
                colour = usage_colour(pct)
                cy = y + ROW * SS // 2
                d.text((BAR_INSET * SS, cy - 7 * SS), name, font=small, fill=MUTED + (255,), anchor="lm")
                left = resets_in(block.get("resets_at"), now)
                right = f"{pct}%" + (f"  ·  resets in {left}" if left else "")
                d.text((W - BAR_INSET * SS, cy - 7 * SS), right, font=small, fill=INK + (255,), anchor="rm")
                mx0, mx1, my = BAR_INSET * SS, W - BAR_INSET * SS, cy + 6 * SS
                d.rounded_rectangle((mx0, my, mx1, my + METER_H * SS), radius=METER_H * SS // 2, fill=(255, 255, 255, 28))
                fw = max(METER_H * SS, (mx1 - mx0) * pct / 100)
                d.rounded_rectangle((mx0, my, mx0 + fw, my + METER_H * SS), radius=METER_H * SS // 2, fill=colour + (255,))
                y += ROW * SS

    img = img.resize((w, h), LANCZOS_FILTER)
    if opaque_key is not None:
        back = Image.new("RGBA", img.size, opaque_key + (255,))
        back.alpha_composite(img)
        img = back
    return img


def session_pids(home: Path | None = None) -> dict[str, int]:
    """session id -> process id, from the per-process files Claude Code keeps in ~/.claude/sessions."""
    out: dict[str, int] = {}
    for path in ((home or Path.home() / ".claude") / "sessions").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("pid"), int) and data.get("sessionId"):
                out[str(data["sessionId"])] = int(data["pid"])
        except (OSError, ValueError, AttributeError):
            continue
    return out


def _lineage(pid: int) -> list[int]:
    """The process and its ancestors: the agent runs inside a terminal or an IDE,
    and it is that window we want in front."""
    try:
        import psutil
        proc = psutil.Process(pid)
        return [pid] + [p.pid for p in proc.parents()]
    except Exception:
        return [pid]


def focus_pid(pid: int) -> bool:
    """Bring the window belonging to `pid` (or the nearest ancestor with one) to the front."""
    pids = _lineage(pid)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in pids:
                found.append((pids.index(owner.value), hwnd))
            return True

        user32.EnumWindows(each, 0)
        if not found:
            return False
        hwnd = min(found)[1]  # the closest ancestor's window wins
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(hwnd))
    if sys.platform == "darwin":
        for p in pids:
            script = f'tell application "System Events" to set frontmost of (first process whose unix id is {p}) to true'
            if subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5).returncode == 0:
                return True
        return False
    import shutil
    if shutil.which("xdotool"):
        for p in pids:
            out = subprocess.run(["xdotool", "search", "--pid", str(p)], capture_output=True, text=True, timeout=5)
            wid = out.stdout.split()
            if wid and subprocess.run(["xdotool", "windowactivate", wid[-1]], timeout=5).returncode == 0:
                return True
    return False


def focus_session(session: dict) -> bool:
    """Click on a row: raise the terminal that tab lives in. Codex has no pid file we know of, so only Claude."""
    pid = session_pids().get(str(session.get("id", "")))
    if pid is None:
        return False
    try:
        return focus_pid(pid)
    except Exception:
        return False


def fullscreen_app_in_front() -> bool:
    """A game, a film, a presentation: the tab stays out of the way while one is up."""
    try:
        if sys.platform == "win32":
            import ctypes
            state = ctypes.c_int()
            if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
                # QUNS_BUSY 2, QUNS_RUNNING_D3D_FULL_SCREEN 3, QUNS_PRESENTATION_MODE 4
                return state.value in (2, 3, 4)
        elif sys.platform == "darwin":
            from AppKit import NSScreen
            screen = NSScreen.mainScreen()
            return screen.visibleFrame().size.height >= screen.frame().size.height  # menu bar gone = full screen
    except Exception:
        pass
    return False


def self_check() -> bool:
    """One frame of each state, drawn off-screen: what CI runs instead of opening a window."""
    from lumen.core.rules import DEFAULT_PALETTE
    rows = [("claude", "check", "running", 42, "Editing api.py", 0.5), ("codex", "check", "input", None, "", None)]
    usage = {"five_hour": {"used": 23, "resets_at": None}, "seven_day": {"used": 72, "resets_at": None}}
    zones = [(240, 170, 40)] * 3 + [(230, 60, 60)] * 3
    a = render(zones, rows, DEFAULT_PALETTE, KEY, [42, None], usage)
    b = render(zones, rows, DEFAULT_PALETTE, None, [42, None], usage, unfolded=True, flip=True, glow_gain=1.5)
    return a.size == (WIDTH, HEIGHT) and b.size[0] == PANEL_WIDTH and b.size[1] > HEIGHT


def _top_inset() -> int:
    """Where the tab hangs from: 0, or the macOS menu bar (notch included)."""
    if sys.platform != "darwin":
        return 0
    try:
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        frame, visible = screen.frame(), screen.visibleFrame()
        return int(frame.size.height - (visible.origin.y + visible.size.height))
    except Exception:
        return 24


def run_child() -> int:
    import io
    import queue
    import threading
    import tkinter as tk

    from lumen.core.rules import DEFAULT_PALETTE

    root = tk.Tk()
    root.withdraw()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    key: RGB | None = KEY
    try:
        if sys.platform == "darwin":
            win.attributes("-transparent", True)
            win.configure(bg="systemTransparent")
            key = None
            # float above full-screen apps without ever taking focus
            root.tk.call("::tk::unsupported::MacWindowStyle", "style", win._w, "help", "noActivates")
        elif sys.platform == "win32":
            win.attributes("-transparentcolor", "#%02x%02x%02x" % KEY)
    except tk.TclError:
        key = KEY
    label = tk.Label(win, bd=0, highlightthickness=0, bg="systemTransparent" if key is None else "#%02x%02x%02x" % KEY)
    label.pack()
    top = _top_inset()

    state = {"zones": [(0, 0, 0)] * ZONE_COUNT, "sessions": [], "usage": {}, "options": {}, "hover": False,
             "photo": None, "pulse_until": 0.0, "waiting": set(), "fullscreen": False, "fs_checked": 0.0,
             "reveal": 1.0}  # reveal: 0..1 of the unfolded panel shown, for the unfold animation

    def position() -> str:
        pos = str(state["options"].get("position") or "top")
        return pos if pos in POSITIONS else "top"

    def show(img):
        pos = position()
        w, h = img.width, img.height
        if pos == "bottom":
            x, y = (sw - w) // 2, sh - h
        elif pos == "top-left":
            x, y = 24, top
        elif pos == "top-right":
            x, y = sw - w - 24, top
        else:
            x, y = (sw - w) // 2, top
        # unfolding: crop the panel to the revealed part so it grows out of the edge
        if state["hover"] and state["reveal"] < 1.0:
            shown = max(HEIGHT, int(HEIGHT + (h - HEIGHT) * state["reveal"]))
            img = img.crop((0, h - shown, w, h)) if pos == "bottom" else img.crop((0, 0, w, shown))
            if pos == "bottom":
                y = sh - shown
            h = shown
        buf = io.BytesIO()
        img.save(buf, "PNG")
        photo = tk.PhotoImage(data=buf.getvalue())
        state["photo"] = photo  # keep a reference or Tk drops the picture
        label.configure(image=photo)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def hide():
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            win.withdraw()

    def draw():
        zones = state["zones"]
        if all(z == (0, 0, 0) for z in zones) or state["fullscreen"]:
            hide()
            return
        opts = state["options"]
        flags = {**SHOW_DEFAULTS, **(opts.get("show") if isinstance(opts.get("show"), dict) else {})}
        rows = apply_show(session_rows(state["sessions"]), flags, str(opts.get("agents") or "all"))
        if not flags.get("sessions", True):
            rows = []
        usage = visible_usage(state["usage"], state["sessions"], flags, str(opts.get("agents") or "all"))
        # one bar per tab (the effect merges same-colour neighbours): only then can a bar carry its tab's context
        fills = [r[3] for r in rows] if len(rows) == len(runs(zones)) else None
        left = state["pulse_until"] - time.time()
        gain = 1.0 + 0.9 * abs(math.sin(left * 4)) if left > 0 else 1.0
        show(render(zones, rows, DEFAULT_PALETTE, key, fills, usage, unfolded=state["hover"],
                    flip=position() == "bottom", glow_gain=gain))
        try:
            win.attributes("-alpha", ALPHA)
        except tk.TclError:
            win.deiconify()

    def animate():
        if state["hover"] and state["reveal"] < 1.0:
            state["reveal"] = min(1.0, state["reveal"] + 0.25)
            draw()
            root.after(16, animate)

    def hover(on):
        if state["hover"] != on:
            state["hover"] = on
            state["reveal"] = 0.0 if on else 1.0
            draw()
            if on:
                root.after(16, animate)

    def clicked(event):
        rows = session_rows(state["sessions"])
        if not state["hover"] or not rows:
            return
        y = event.y if position() != "bottom" else event.y  # the image is not mirrored, only anchored
        i = panel_row_at(int(y), len(rows))
        if i is None:
            return
        ordered = sorted(state["sessions"], key=lambda s: int(s.get("slot", 0)))
        threading.Thread(target=focus_session, args=(ordered[i],), daemon=True).start()

    label.bind("<Button-1>", clicked)

    def pointer_inside() -> bool:
        px, py = win.winfo_pointerxy()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        return x <= px < x + win.winfo_width() and y <= py < y + win.winfo_height()

    # Unfolding resizes the window under the pointer, which Tk reports as a
    # Leave: only fold back once the pointer has really left the (new) tab.
    win.bind("<Enter>", lambda e: hover(True))
    win.bind("<Leave>", lambda e: root.after(150, lambda: None if pointer_inside() else hover(False)))

    lines: queue.Queue = queue.Queue()

    def reader():
        for line in sys.stdin:
            lines.put(line.strip())
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def poll():
        last = None
        while True:
            try:
                item = lines.get_nowait()
            except queue.Empty:
                break
            if item is None:
                root.destroy()
                return
            last = item
        if last and (parsed := parse_line(last)) is not None and list(parsed) != [state["zones"], state["sessions"],
                                                                                    state["usage"], state["options"]]:
            state["zones"], state["sessions"], state["usage"], state["options"] = parsed
            # a tab that just started waiting on you: breathe for a moment so the eye catches it
            waiting = {s.get("id") for s in state["sessions"] if s.get("status") == "input"}
            if waiting - state["waiting"]:
                state["pulse_until"] = time.time() + PULSE_S
            state["waiting"] = waiting
            draw()
        elif state["pulse_until"] > time.time():
            draw()
        now = time.time()
        if now - state["fs_checked"] > 1.0:  # once a second: is a game or a film in front?
            state["fs_checked"] = now
            fs = bool(state["options"].get("hide_fullscreen", True)) and fullscreen_app_in_front()
            if fs != state["fullscreen"]:
                state["fullscreen"] = fs
                draw()
        root.after(16 if state["pulse_until"] > now else 33, poll)

    draw()
    root.after(16, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_child())
