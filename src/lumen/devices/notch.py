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

import functools
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
PANEL_WIDTH, ROW, PANEL_PAD = 460, 30, 12
USAGE_W = 58                 # room for the remaining percentage in the folded tab
METER_H = 4
BAR_H, BAR_GAP, BAR_INSET = 6, 6, 20
ALPHA = 0.96
SHELL = (12, 13, 17)
EDGE = (255, 255, 255, 22)   # 1px inner hairline
INK, MUTED = (236, 237, 241), (140, 143, 154)
KEY = (1, 0, 1)              # Windows chroma key; on macOS the window is truly transparent
LABELS = {"running": "working", "input": "needs you", "done": "done"}
SS = 3                       # supersample factor for Pillow drawing
POSITIONS = ("top", "top-left", "top-right", "bottom", "left", "right")
VERTICAL = ("left", "right")  # the folded tab is turned on its side; the panel opens upright
# One accent per agent, as a cap on its bar: which agent a bar is at a glance,
# whatever state colour the bar itself has.
AGENT_ACCENT: dict[str, RGB] = {
    "claude": (217, 119, 87),
    "codex": (120, 150, 255),
    "gemini": (66, 133, 244),
    "google": (66, 133, 244),
    "antigravity": (66, 133, 244),
}
# Thin, regular, thick: the folded tab's height, its bar height and its width.
# The unfolded panel keeps its own row height; only the tab itself scales.
SIZES = {"thin": (16, 4, 200), "regular": (HEIGHT, BAR_H, WIDTH), "thick": (34, 8, 280)}
# Everything the tab can show, all on by default. Settings turns pieces off:
# someone who only wants the Claude limits and the live tabs unticks the rest.
SHOW_DEFAULTS = {"sessions": True, "context": True, "cost": True, "activity": True,
                 "claude_usage": True, "codex_usage": True, "gemini_usage": True, "accent": True, "usage_follows_tabs": False}
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
        # Session metadata, usage and display options can change without new colors.
        self.refresh_every_s = 0.5
        self.ambient = True  # its whole purpose is the persistent sessions view
        self.details = {"connection": "built-in", "zones": ZONE_COUNT}
        self.position, self.hide_fullscreen = "top", True
        self.offset, self.size, self.opacity = -1, "regular", 96  # along the edge (%), thin/regular/thick, %
        self.idle_hide_min = 0            # hide once no agent has done anything for this long; 0 = never
        self.completed_hide_min = 30      # retire completed rows; running and input rows always stay visible
        self.port = 6733                  # the dashboard, so a drag can save its new place
        self.show = dict(SHOW_DEFAULTS)   # what the tab displays; Settings can trim it to "just my limits"
        self.agents = "all"               # whose sessions: all | claude | codex | gemini
        self.theme = "liquid"             # liquid | dynamic | minimal | rog | studio

    def child_command(self) -> list[str]:
        return child_command()

    def set_color(self, rgb) -> None:
        self.set_zones([tuple(rgb)] * ZONE_COUNT)

    def set_zones(self, colors: list[RGB]) -> None:
        from lumen.integrations import claude_usage, codex_usage, google_usage
        from lumen.integrations.agent_sessions import all_sessions
        payload = {"sessions": all_sessions(),
                   "usage": {"claude": claude_usage.latest(), "codex": codex_usage.latest(), "gemini": google_usage.latest()},
                   "options": {"position": self.position, "offset": self.offset, "size": self.size,
                               "theme": getattr(self, "theme", "liquid"),
                               "opacity": self.opacity, "port": self.port, "hide_fullscreen": self.hide_fullscreen,
                               "idle_hide_min": self.idle_hide_min,
                               "completed_hide_min": self.completed_hide_min,
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
    s = settings or {}
    _instance.offset = max(-1, min(100, int(s.get("notch_offset", -1) or -1)))
    size = str(s.get("notch_size") or "regular")
    _instance.size = size if size in SIZES else "regular"
    theme = str(s.get("notch_theme") or "liquid")
    _instance.theme = theme if theme in ("liquid", "dynamic", "minimal", "rog", "studio") else "liquid"
    _instance.opacity = max(30, min(100, int(s.get("notch_opacity", 96) or 96)))
    _instance.port = int(s.get("port", 6733) or 6733)
    _instance.idle_hide_min = max(0, int(s.get("notch_idle_hide_min", 0) or 0))
    _instance.completed_hide_min = max(0, int(s.get("notch_completed_hide_min", 30) or 0))
    _instance.show = {k: bool((settings or {}).get(f"notch_show_{k}", v)) for k, v in SHOW_DEFAULTS.items()}
    agents = str((settings or {}).get("notch_agents") or "all")
    _instance.agents = agents if agents in ("all", "claude", "codex", "gemini") else "all"
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
    if any(k == "five_hour" or k.startswith("seven_day") for k in usage):
        usage = {"claude": usage}
    out = {}
    for agent, summary in usage.items():
        summary = summary if isinstance(summary, dict) else {}
        windows = {"claude": ("five_hour", "seven_day"), "codex": ("seven_day",),
                   "gemini": ("five_hour", "seven_day"), "google": ("five_hour", "seven_day")}.get(agent, ())
        blocks = {k: summary[k] for k in windows if isinstance(summary.get(k), dict)
                  and isinstance(summary[k].get("used"), (int, float))}
        if windows:
            out[str(agent)] = blocks
    return out


USAGE_COLORS = {"claude": (226, 151, 113), "codex": (105, 167, 255),
                "gemini": (181, 152, 255), "google": (181, 152, 255)}


def remaining(block: dict) -> float:
    return round(100 - max(0, min(100, float(block["used"]))), 2)


def remaining_label(block: dict) -> str:
    return f"{remaining(block):.2f}".rstrip("0").rstrip(".") + "% left"


def bar_window(agent: str, summary: dict) -> str | None:
    key = "seven_day" if agent == "codex" else "five_hour"
    return key if key in summary else None


def usage_layout(usage: dict, row_count: int, expanded=(), height: int = HEIGHT) -> list[tuple]:
    """Shared logical coordinates for painting and clicking provider headers."""
    y = height + PANEL_PAD + ROW * row_count
    if row_count and usage:
        y += PANEL_PAD // 2
    out = []
    for agent, summary in usage.items():
        detail_height = ROW + 16 if agent in expanded and bar_window(agent, summary) else 0
        out.append((agent, summary, y, detail_height))
        y += ROW + detail_height
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


def visible_usage(usage: dict, sessions: list[dict], show: dict, agents: str = "all", options: dict | None = None) -> dict:
    """Follow present task rows, including recent completions, not old history.

    Hiding task details is independent of displaying the provider's limits.
    """
    present = visible_sessions([s for s in sessions if isinstance(s, dict)],
                               {**(options or {}), "show": {"sessions": True}})
    live = {str(s.get("agent") or "") for s in present}
    follow = show.get("usage_follows_tabs", False)  # the 0.7.1 rule, now opt-in: hide a closed agent's meter
    return {a: s for a, s in usage.items()
            if (a in live or not follow) and show.get(f"{a}_usage", True) and (agents == "all" or a == agents)}


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


def _session_order(session: dict) -> tuple[int, int]:
    return ({"running": 0, "input": 1, "done": 2}.get(str(session.get("status")), 3),
            int(session.get("slot", 0)))


def session_rows(sessions: list[dict], *, prioritize: bool = True) -> list[tuple[str, str, str, int | None, str, float | None]]:
    """Rows prioritize running tasks; bar metadata can request physical slot order."""
    from lumen.integrations.agent_sessions import context_percent
    rows = sorted(sessions, key=_session_order if prioritize else lambda s: int(s.get("slot", 0)))
    out = []
    for s in rows:
        cost = s.get("cost_usd")
        out.append((str(s.get("agent", "agent")),
                    str(s.get("label") or paths.basename(str(s.get("cwd") or "")) or s.get("title") or "~"),
                    str(s.get("status")), context_percent(s), str(s.get("activity") or ""),
                    float(cost) if isinstance(cost, (int, float)) else None))
    return out


def visible_sessions(sessions: list[dict], options: dict, now: float | None = None) -> list[dict]:
    """The session records represented by visible rows, in row order.

    Completed tabs age out of the status tab, while working and input tabs stay
    visible.  This is display-only: the dashboard keeps the complete session
    history until the agent itself closes it.
    """
    raw_show = options.get("show")
    show: dict = raw_show if isinstance(raw_show, dict) else {}
    if not bool(show.get("sessions", True)):
        return []
    agent = str(options.get("agents") or "all")
    hide_after = options.get("completed_hide_min", 30)
    minutes = max(0, int(hide_after)) if isinstance(hide_after, (int, float)) else 30
    current = time.time() if now is None else now

    def keep(session: dict) -> bool:
        if agent != "all" and session.get("agent") != agent:
            return False
        if minutes <= 0 or session.get("status") != "done":
            return True
        stamp = session.get("ts")
        return not isinstance(stamp, (int, float)) or current - stamp < minutes * 60

    return [s for s in sorted(sessions, key=_session_order)
            if keep(s)]


def tab_x(screen_w: int, tab_w: int, position: str, offset: int = -1, margin: int = 24) -> int:
    """Where the tab's left edge goes. `offset` is its centre along the edge as
    a percentage of the screen width (a drag sets it); -1 means the preset
    corner the position names. Never past either side of the screen."""
    if offset is None or offset < 0:
        if position == "top-left":
            return margin
        if position == "top-right":
            return screen_w - tab_w - margin
        return (screen_w - tab_w) // 2
    x = int(screen_w * min(100, offset) / 100) - tab_w // 2
    return max(0, min(screen_w - tab_w, x))


def offset_for(screen_w: int, tab_w: int, x: int) -> int:
    """The inverse of tab_x: the percentage to save for a tab whose left edge is at x."""
    return max(0, min(100, round((x + tab_w / 2) * 100 / screen_w))) if screen_w > 0 else 50


def idle_hidden(sessions: list[dict], idle_min: int, now: float | None = None) -> bool:
    """True once nothing has happened for `idle_min` minutes: no tab is working
    or waiting on you, and the newest status change is older than that. 0 = never."""
    if idle_min <= 0:
        return False
    live = [s for s in sessions if isinstance(s, dict)]
    if any(s.get("status") in ("running", "input") for s in live):
        return False
    stamps = [float(s["ts"]) for s in live if isinstance(s.get("ts"), (int, float))]
    last = max(stamps) if stamps else 0.0
    return (now or time.time()) - last > idle_min * 60


def bar_accents(rows: list[tuple], show: bool = True) -> list[RGB | None] | None:
    """One agent accent per bar, in row order, or None when accents are off."""
    return [AGENT_ACCENT.get(str(r[0])) for r in rows] if show else None


def panel_row_at(y: int, n_rows: int, height: int = HEIGHT) -> int | None:
    """Which session row a click at image y (logical px) landed on, or None."""
    top = height + PANEL_PAD
    if y < top:
        return None
    i = (y - top) // ROW
    return int(i) if 0 <= i < n_rows else None


@functools.lru_cache(maxsize=16)
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
           unfolded: bool = False, now: float | None = None, flip: bool = False, glow_gain: float = 1.0,
           size: str = "regular", rounded: bool = False, accents: list[RGB | None] | None = None,
           theme: str = "dynamic", expanded_usage=()):
    """The tab as an RGBA Pillow image (composited onto `opaque_key` where the
    platform can't do per-pixel alpha). `unfolded` = the hover panel: session
    rows, then the usage meters. `fills` is one context-window percentage per
    bar (None = solid bar); `usage` the 5-hour / 7-day summary, if known.
    `flip` rounds the top instead (the tab hangs up from the bottom edge);
    `glow_gain` > 1 brightens the bar glow for the "needs you" pulse; `size`
    picks the folded tab's thickness (thin | regular | thick); `rounded` rounds
    every corner (the panel beside a left/right tab touches no edge); `accents`
    is one agent colour per bar (None = no cap), drawn as a cap on the bar."""
    from PIL import Image, ImageDraw, ImageFilter

    height, bar_h, width = SIZES.get(size, SIZES["regular"])

    from lumen.integrations.claude_usage import resets_in

    by_agent = clean_usage(usage or {})
    meters = usage_layout(by_agent, len(rows), expanded_usage, height) if unfolded else []
    five = next((s[k]["used"] for s in by_agent.values() for k in ("five_hour", "daily", "seven_day") if k in s), None)

    BTN_H = 22
    w = PANEL_WIDTH if unfolded else width
    h = height
    if unfolded:
        content_end = meters[-1][2] + ROW + meters[-1][3] if meters else height + PANEL_PAD + ROW * len(rows)
        h = content_end + 7 + BTN_H + PANEL_PAD
    W, H = w * SS, h * SS

    # Theme aesthetics: shell fill, stroke edge, and corner radius
    if theme == "liquid":
        shell_fill = (8, 10, 15)
        edge_stroke = (190, 215, 255, 45)  # Liquid mercury rim
        corner_r = (24 if not unfolded else 32) * SS
    elif theme == "rog":
        shell_fill = (8, 10, 14)
        edge_stroke = (0, 240, 255, 65)  # Cyber neon cyan edge
        corner_r = 4 * SS
    elif theme == "minimal":
        shell_fill = (15, 16, 20)
        edge_stroke = (255, 255, 255, 12)
        corner_r = 8 * SS
    elif theme == "studio":
        shell_fill = (24, 25, 30)
        edge_stroke = (255, 255, 255, 36)
        corner_r = 14 * SS
    else:  # dynamic
        shell_fill = SHELL
        edge_stroke = EDGE
        corner_r = RADIUS * SS

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # shell: flat on the screen-edge side, rounded on the other — it hangs from the edge
    if rounded:
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=corner_r, fill=shell_fill + (255,))
        d.rounded_rectangle((SS, SS, W - 1 - SS, H - 1 - SS), radius=max(SS, corner_r - SS), outline=edge_stroke, width=SS)
    elif flip:
        d.rounded_rectangle((0, 0, W - 1, H - 1 + corner_r), radius=corner_r, fill=shell_fill + (255,))
        d.rounded_rectangle((SS, SS, W - 1 - SS, H - 1 + corner_r), radius=max(SS, corner_r - SS), outline=edge_stroke, width=SS)
    else:
        d.rounded_rectangle((0, -corner_r, W - 1, H - 1), radius=corner_r, fill=shell_fill + (255,))
        d.rounded_rectangle((SS, -corner_r, W - 1 - SS, H - 1 - SS), radius=max(SS, corner_r - SS), outline=edge_stroke, width=SS)

    # Specular liquid glass highlights for liquid theme
    if theme == "liquid":
        spec_y = 5 * SS if not flip else H - 7 * SS
        spec_w = max(50 * SS, W // 3)
        d.rounded_rectangle(((W - spec_w) // 2, spec_y, (W + spec_w) // 2, spec_y + 2 * SS),
                            radius=SS, fill=(255, 255, 255, 75))

    # session bars, each lit from beneath
    bars = runs(zones)
    if bars:
        inner = W - 2 * BAR_INSET * SS - (USAGE_W * SS if five is not None and not unfolded else 0)
        total = sum(n for _, n in bars)
        gap = BAR_GAP * SS if len(bars) > 1 else 0
        unit = (inner - gap * (len(bars) - 1)) / total
        y0 = (height * SS - bar_h * SS) // 2
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        x = BAR_INSET * SS
        for colour, n in bars:
            x1 = x + unit * n
            gd.rounded_rectangle((x - 2 * SS, y0 - 2 * SS, x1 + 2 * SS, y0 + (bar_h + 2) * SS), radius=bar_h * SS,
                                 fill=colour + (int(min(255, 150 * glow_gain)),))
            x = x1 + gap
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4 * SS)))
        d = ImageDraw.Draw(img)
        x = BAR_INSET * SS
        for i, (colour, n) in enumerate(bars):
            x1 = x + unit * n
            pct = fills[i] if fills and i < len(fills) else None
            r = bar_h * SS // 2
            if pct is None:
                d.rounded_rectangle((x, y0, x1, y0 + bar_h * SS), radius=r, fill=colour + (255,))
            else:  # a dim track, filled to how much of the context window the tab has used
                d.rounded_rectangle((x, y0, x1, y0 + bar_h * SS), radius=r, fill=colour + (70,))
                fill_w = max(bar_h * SS, (x1 - x) * max(0, min(100, pct)) / 100)
                d.rounded_rectangle((x, y0, x + fill_w, y0 + bar_h * SS), radius=r, fill=colour + (255,))
            accent = accents[i] if accents and i < len(accents) else None
            if accent is not None:  # the agent's colour as a cap on the bar's leading end
                cap = (bar_h + 2) * SS
                d.ellipse((x - SS, y0 - SS, x - SS + cap, y0 - SS + cap), fill=accent + (255,))
            x = x1 + gap

    if five is not None and not unfolded:  # only remaining percentage; window labels belong in the panel
        f = _font(11 * SS, bold=True)
        colour = usage_colour(int(five))
        d.text((W - BAR_INSET * SS, height * SS // 2), remaining_label({"used": five}).removesuffix(" left"), font=f, fill=colour + (255,), anchor="rm")

    if unfolded:
        meta_f = _font(12 * SS)
        badge_f = _font(10 * SS, bold=True)
        d.line((BAR_INSET * SS, height * SS + 2 * SS, W - BAR_INSET * SS, height * SS + 2 * SS), fill=EDGE, width=SS)
        y = (height + PANEL_PAD) * SS
        if not rows and not meters:
            d.text((W // 2, y + ROW * SS // 2), "No open agent tabs", font=meta_f, fill=MUTED + (255,), anchor="mm")
        for row in rows:
            agent, folder, status, pct = row[:4]
            activity = row[4] if len(row) > 4 else ""
            cost = row[5] if len(row) > 5 else None
            colour = tuple(palette.get(status, MUTED[:3]))
            accent = AGENT_ACCENT.get(agent, (140, 143, 154))
            cy = y + ROW * SS // 2
            # Status indicator
            if theme == "liquid":
                d.ellipse((BAR_INSET * SS - 2 * SS, cy - 6 * SS, BAR_INSET * SS + 10 * SS, cy + 6 * SS),
                          outline=colour + (70,), width=SS)
                d.ellipse((BAR_INSET * SS, cy - 4 * SS, BAR_INSET * SS + 8 * SS, cy + 4 * SS), fill=colour + (255,))
            else:
                d.ellipse((BAR_INSET * SS, cy - 4 * SS, BAR_INSET * SS + 8 * SS, cy + 4 * SS), fill=colour + (255,))

            # Agent badge pill
            agent_tag = "Antigravity" if agent in ("gemini", "antigravity") else agent.capitalize()
            if theme == "liquid":
                tag_w = d.textlength(agent_tag, font=badge_f)
                d.rounded_rectangle((BAR_INSET * SS + 14 * SS, cy - 8 * SS, BAR_INSET * SS + 24 * SS + tag_w, cy + 8 * SS),
                                    radius=7 * SS, fill=accent + (45,), outline=(190, 215, 255, 80), width=SS)
                d.line((BAR_INSET * SS + 17 * SS, cy - 6 * SS, BAR_INSET * SS + 21 * SS + tag_w, cy - 6 * SS),
                       fill=(255, 255, 255, 90), width=SS)
                d.text((BAR_INSET * SS + 19 * SS, cy), agent_tag, font=badge_f, fill=INK + (255,), anchor="lm")
                nx = tag_w + 10 * SS
            elif theme == "rog":
                agent_tag = f"[{agent_tag.upper()}]"
                tag_w = d.textlength(agent_tag, font=badge_f)
                d.rectangle((BAR_INSET * SS + 14 * SS, cy - 7 * SS, BAR_INSET * SS + 22 * SS + tag_w, cy + 7 * SS),
                            fill=(0, 0, 0, 160), outline=accent + (200,), width=SS)
                d.text((BAR_INSET * SS + 18 * SS, cy), agent_tag, font=badge_f, fill=accent + (255,), anchor="lm")
                nx = tag_w + 8 * SS
            elif theme == "minimal":
                tag_w = d.textlength(agent_tag, font=badge_f)
                d.text((BAR_INSET * SS + 14 * SS, cy), agent_tag, font=badge_f, fill=accent + (255,), anchor="lm")
                nx = tag_w
            else:  # dynamic & studio
                tag_w = d.textlength(agent_tag, font=badge_f)
                d.rounded_rectangle((BAR_INSET * SS + 14 * SS, cy - 8 * SS, BAR_INSET * SS + 22 * SS + tag_w, cy + 8 * SS),
                                    radius=4 * SS, fill=accent + (40,), outline=accent + (95,), width=SS)
                d.text((BAR_INSET * SS + 18 * SS, cy), agent_tag, font=badge_f, fill=INK + (255,), anchor="lm")
                nx = tag_w + 8 * SS

            label = cast(str, LABELS.get(status, status))
            d.text((W - BAR_INSET * SS, cy), label, font=meta_f, fill=colour + (255,), anchor="rm")
            right = W - BAR_INSET * SS - d.textlength(label, font=meta_f) - 12 * SS
            meta = "  ·  ".join(x for x in ((f"${cost:.2f}" if cost else ""), (f"{pct}%" if pct is not None else "")) if x)
            if meta:  # cost and context window, e.g. "$1.20  ·  63%", left of the state
                d.text((right, cy), meta, font=meta_f, fill=MUTED + (255,), anchor="rm")
                right -= d.textlength(meta, font=meta_f) + 12 * SS
            # name, then what the tab is doing right now; clipped to the room that is left
            text = folder + (f"  ·  {activity}" if activity and status == "running" else "")
            x0 = BAR_INSET * SS + 22 * SS + nx + 8 * SS
            while text and d.textlength(text, font=meta_f) > right - x0:
                text = text[:-2].rstrip() + "…" if len(text) > 2 else ""
            d.text((x0, cy), text, font=meta_f, fill=MUTED + (255,), anchor="lm")
            y += ROW * SS
        if meters:
            small = _font(11 * SS)
            for agent, summary, top_y, detail_height in meters:
                y = top_y * SS
                cy = y + ROW * SS // 2
                name = "Antigravity" if agent in ("gemini", "google") else agent.capitalize()
                d.line((BAR_INSET * SS, y, W - BAR_INSET * SS, y), fill=edge_stroke, width=SS)
                d.text((BAR_INSET * SS, cy), name, font=meta_f, fill=INK + (255,), anchor="lm")
                right = W - BAR_INSET * SS
                if summary:
                    d.text((right, cy), "−" if detail_height else "+" if bar_window(agent, summary) else "", font=meta_f, fill=MUTED + (255,), anchor="rm")
                    right -= 18 * SS
                    for k, block in reversed(list(summary.items())):
                        text = ("5h" if k == "five_hour" else "7d") + f" · {remaining_label(block)}"
                        tw = d.textlength(text, font=small)
                        d.rounded_rectangle((right - tw - 10 * SS, cy - 9 * SS, right, cy + 9 * SS),
                                            radius=4 * SS, fill=(35, 41, 52, 255))
                        d.text((right - 5 * SS, cy), text, font=small, fill=USAGE_COLORS[agent] + (255,), anchor="rm")
                        right -= tw + 16 * SS
                else:
                    d.text((right, cy), "Usage unavailable", font=small, fill=MUTED + (255,), anchor="rm")
                if detail_height:
                    k = bar_window(agent, summary)
                    block = summary[k]
                    x, x1 = BAR_INSET * SS, W - BAR_INSET * SS
                    my = y + (ROW + 2) * SS
                    d.rounded_rectangle((x, my, x1, my + METER_H * SS), radius=SS, fill=(39, 45, 55, 255))
                    pct = remaining(block)
                    if pct:
                        d.rounded_rectangle((x, my, x + (x1 - x) * pct / 100, my + METER_H * SS),
                                            radius=SS, fill=USAGE_COLORS[agent] + (255,))
                    reset = resets_in(block.get("resets_at"), now)
                    caption = ("5-hour" if k == "five_hour" else "7-day") + " · " + remaining_label(block)
                    d.text((x, my + 12 * SS), caption, font=small, fill=USAGE_COLORS[agent] + (255,))
                    d.text((x1, my + 12 * SS), f"Resets in {reset}" if reset else "Reset unavailable",
                           font=small, fill=MUTED + (255,), anchor="ra")
                y = (top_y + ROW + detail_height) * SS

        # Bottom quick action controls: [ ⚡ Dashboard ] [ ✕ Clear Done ]
        d.line((BAR_INSET * SS, y + 2 * SS, W - BAR_INSET * SS, y + 2 * SS), fill=edge_stroke, width=SS)
        btn_y = y + 7 * SS
        btn_f = _font(11 * SS, bold=True)
        btn_w = (W - 2 * BAR_INSET * SS - 10 * SS) // 2
        # Dashboard button
        b1_x0, b1_x1 = BAR_INSET * SS, BAR_INSET * SS + btn_w
        b2_x0, b2_x1 = b1_x1 + 10 * SS, W - BAR_INSET * SS
        if theme == "liquid":
            btn_r = 10 * SS
            d.rounded_rectangle((b1_x0, btn_y, b1_x1, btn_y + BTN_H * SS), radius=btn_r,
                                 fill=(22, 26, 36, 220), outline=(190, 215, 255, 60), width=SS)
            d.line((b1_x0 + 8 * SS, btn_y + 2 * SS, b1_x1 - 8 * SS, btn_y + 2 * SS), fill=(255, 255, 255, 75), width=SS)
            d.text(((b1_x0 + b1_x1) // 2, btn_y + (BTN_H * SS) // 2), "⚡ Dashboard", font=btn_f, fill=INK + (255,), anchor="mm")

            d.rounded_rectangle((b2_x0, btn_y, b2_x1, btn_y + BTN_H * SS), radius=btn_r,
                                 fill=(22, 26, 36, 220), outline=(190, 215, 255, 60), width=SS)
            d.line((b2_x0 + 8 * SS, btn_y + 2 * SS, b2_x1 - 8 * SS, btn_y + 2 * SS), fill=(255, 255, 255, 75), width=SS)
            d.text(((b2_x0 + b2_x1) // 2, btn_y + (BTN_H * SS) // 2), "✕ Clear Done", font=btn_f, fill=MUTED + (255,), anchor="mm")
        elif theme == "rog":
            d.rectangle((b1_x0, btn_y, b1_x1, btn_y + BTN_H * SS), fill=(0, 240, 255, 20), outline=(0, 240, 255, 90), width=SS)
            d.text(((b1_x0 + b1_x1) // 2, btn_y + (BTN_H * SS) // 2), "⚡ DASHBOARD", font=btn_f, fill=(0, 240, 255, 255), anchor="mm")
            d.rectangle((b2_x0, btn_y, b2_x1, btn_y + BTN_H * SS), fill=(255, 0, 85, 20), outline=(255, 0, 85, 90), width=SS)
            d.text(((b2_x0 + b2_x1) // 2, btn_y + (BTN_H * SS) // 2), "✕ CLEAR DONE", font=btn_f, fill=(255, 120, 150, 255), anchor="mm")
        else:
            btn_r = 4 * SS if theme == "minimal" else 8 * SS if theme == "studio" else 6 * SS
            d.rounded_rectangle((b1_x0, btn_y, b1_x1, btn_y + BTN_H * SS), radius=btn_r,
                                 fill=(255, 255, 255, 14), outline=edge_stroke, width=SS)
            d.text(((b1_x0 + b1_x1) // 2, btn_y + (BTN_H * SS) // 2), "⚡ Dashboard", font=btn_f, fill=INK + (255,), anchor="mm")
            d.rounded_rectangle((b2_x0, btn_y, b2_x1, btn_y + BTN_H * SS), radius=btn_r,
                                 fill=(255, 255, 255, 14), outline=edge_stroke, width=SS)
            d.text(((b2_x0 + b2_x1) // 2, btn_y + (BTN_H * SS) // 2), "✕ Clear Done", font=btn_f, fill=MUTED + (255,), anchor="mm")

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
    """Click on a row: raise the terminal/editor that tab lives in. Works for Claude, and now for Antigravity/Gemini on Windows/macOS!"""
    agent = session.get("agent")
    if agent in ("gemini", "antigravity", "google"):
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            title = str(session.get("title") or "")
            conv_id = str(session.get("id") or "")
            found: list[int] = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def enum_proc(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                w_title = buff.value
                if "Antigravity" in w_title or (title and title in w_title) or (conv_id and conv_id[:8] in w_title):
                    found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(enum_proc, 0)
            if found:
                hwnd = found[0]
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return True
        return False

    if agent == "codex":
        return False
    pid = session_pids().get(str(session.get("id", "")))
    if pid is None:
        return False
    try:
        return focus_pid(pid)
    except Exception:
        return False


def save_setting(port, settings: dict) -> bool:
    """Write a setting back through the daemon's own API (the child has no
    config file of its own). False when the dashboard is not answering."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{int(port or 6733)}/api/settings", method="PUT",
                                     data=json.dumps(settings).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return 200 <= r.status < 300
    except (OSError, ValueError):
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
    rows = [("claude", "check", "running", 42, "Editing api.py", 0.5),
            ("codex", "check", "input", None, "", None),
            ("gemini", "check", "done", 10, "Planning", None)]
    usage = {"five_hour": {"used": 23, "resets_at": None}, "seven_day": {"used": 72, "resets_at": None},
             "gemini": {"daily": {"used": 15}}}
    zones = [(240, 170, 40)] * 2 + [(230, 60, 60)] * 2 + [(0, 240, 48)] * 2
    a = render(zones, rows, DEFAULT_PALETTE, KEY, [42, None, 10], usage)
    b = render(zones, rows, DEFAULT_PALETTE, None, [42, None, 10], usage, unfolded=True, flip=True, glow_gain=1.5)
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
    import queue
    import threading
    import tkinter as tk

    from PIL import ImageTk

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
             "photo": None, "image": None, "pulse_until": 0.0, "waiting": set(), "fullscreen": False, "fs_checked": 0.0,
             "reveal": 1.0,  # reveal: 0..1 of the unfolded panel shown, for the unfold animation
             "offset": None, "offset_hold": 0.0,  # a dragged position, kept until the daemon confirms it
             "drag": None, "pinned": False, "expanded_usage": set()}       # drag: (pointer x at press, offset then); pinned: stays unfolded

    def position() -> str:
        pos = str(state["options"].get("position") or "top")
        return pos if pos in POSITIONS else "top"

    def size() -> str:
        s = str(state["options"].get("size") or "regular")
        return s if s in SIZES else "regular"

    def offset() -> int:
        if state["offset"] is not None and (state["drag"] or time.time() < state["offset_hold"]):
            return state["offset"]
        state["offset"] = None
        raw = state["options"].get("offset", -1)
        return int(raw) if isinstance(raw, (int, float)) else -1

    def show(img):
        pos = position()
        folded_h = SIZES[size()][0]
        if pos in VERTICAL:
            # the folded tab lies along the edge, turned so its flat side touches
            # it; the panel opens upright beside it, from the same spot
            if not state["hover"]:
                img = img.rotate(90 if pos == "left" else -90, expand=True)
            w, h = img.width, img.height
            y = tab_x(sh, h, "top", offset())
            if state["hover"] and state["reveal"] < 1.0:
                shown = max(folded_h, int(folded_h + (w - folded_h) * state["reveal"]))
                img = img.crop((w - shown, 0, w, h)) if pos == "right" else img.crop((0, 0, shown, h))
                w = shown
            x = 0 if pos == "left" else sw - w
        else:
            w, h = img.width, img.height
            x = tab_x(sw, w, pos, offset())
            y = sh - h if pos == "bottom" else top
            # unfolding: crop the panel to the revealed part so it grows out of the edge
            if state["hover"] and state["reveal"] < 1.0:
                shown = max(folded_h, int(folded_h + (h - folded_h) * state["reveal"]))
                img = img.crop((0, h - shown, w, h)) if pos == "bottom" else img.crop((0, 0, w, shown))
                if pos == "bottom":
                    y = sh - shown
                h = shown
        photo = ImageTk.PhotoImage(img, master=root)
        state["photo"] = photo  # keep a reference or Tk drops the picture
        label.configure(image=photo)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def hide():
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            win.withdraw()

    def draw(*, refresh: bool = True):
        zones = state["zones"]
        opts = state["options"]
        idle = opts.get("idle_hide_min")
        if all(z == (0, 0, 0) for z in zones) or state["fullscreen"] or \
                (not state["pinned"] and idle_hidden(state["sessions"], int(idle) if isinstance(idle, (int, float)) else 0)):
            hide()
            return
        if not refresh and state["image"] is not None:
            show(state["image"])
            return
        flags = {**SHOW_DEFAULTS, **(opts.get("show") if isinstance(opts.get("show"), dict) else {})}
        shown_sessions = visible_sessions(state["sessions"], opts)
        rows = apply_show(session_rows(shown_sessions), flags)
        bar_rows = apply_show(session_rows(shown_sessions, prioritize=False), flags)
        usage = visible_usage(state["usage"], state["sessions"], flags, str(opts.get("agents") or "all"), opts)
        # one bar per tab (the effect merges same-colour neighbours): only then can a bar carry its tab's context
        fills = [r[3] for r in bar_rows] if len(bar_rows) == len(runs(zones)) else None
        left = state["pulse_until"] - time.time()
        gain = 1.0 + 0.9 * abs(math.sin(left * 4)) if left > 0 else 1.0
        accents = bar_accents(bar_rows, flags.get("accent", True)) if fills is not None else None
        state["image"] = render(zones, rows, DEFAULT_PALETTE, key, fills, usage, unfolded=state["hover"],
                    flip=position() == "bottom", glow_gain=gain, size=size(),
                    rounded=state["hover"] and position() in VERTICAL, accents=accents,
                    theme=str(opts.get("theme") or "dynamic"), expanded_usage=state["expanded_usage"])
        show(state["image"])
        opacity = opts.get("opacity")
        alpha = max(0.3, min(1.0, opacity / 100)) if isinstance(opacity, (int, float)) else ALPHA
        try:
            win.attributes("-alpha", alpha)
        except tk.TclError:
            win.deiconify()

    def animate():
        if state["hover"] and state["reveal"] < 1.0:
            state["reveal"] = min(1.0, state["reveal"] + 0.25)
            draw(refresh=False)
            root.after(16, animate)

    def hover(on):
        if not on and state["pinned"]:
            return  # double-clicked open: stays until double-clicked again
        if state["hover"] != on:
            state["hover"] = on
            state["reveal"] = 0.0 if on else 1.0
            draw()
            if on:
                root.after(16, animate)

    def clicked(event):
        if not state["hover"]:
            return
        port = state["options"].get("port", 6733)
        y, x = event.y, event.x
        h = SIZES[size()][0]
        sessions = visible_sessions(state["sessions"], state["options"])
        total_h = win.winfo_height()
        if y >= total_h - 34:
            if x < PANEL_WIDTH // 2:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{port}/")
            else:
                from lumen.integrations.agent_sessions import forget_session
                for s in list(sessions):
                    if s.get("status") == "done":
                        forget_session(str(s.get("id", "")))
                draw()
            return
        opts = state["options"]
        flags = {**SHOW_DEFAULTS, **(opts.get("show") if isinstance(opts.get("show"), dict) else {})}
        usage = visible_usage(state["usage"], state["sessions"], flags, str(opts.get("agents") or "all"), opts)
        for agent, summary, top_y, _ in usage_layout(usage, len(sessions), state["expanded_usage"], h):
            if top_y <= y < top_y + ROW:
                if bar_window(agent, summary):
                    state["expanded_usage"].symmetric_difference_update({agent})
                    state["reveal"] = 1.0
                    draw()
                return
        if not sessions:
            return
        i = panel_row_at(int(y), len(sessions), h)
        if i is None or i >= len(sessions):
            return
        session = sessions[i]
        if session.get("agent") in ("claude", "gemini", "google", "antigravity"):
            threading.Thread(target=focus_session, args=(session,), daemon=True).start()

    # Drag the tab along its edge to put it anywhere; letting go saves the spot
    # through the dashboard's settings API, so it is back there next start.
    def along():
        """(screen length, tab length, tab start, pointer) along the edge the tab sits on."""
        if position() in VERTICAL:
            return sh, win.winfo_height(), win.winfo_rooty(), win.winfo_pointery()
        return sw, win.winfo_width(), win.winfo_rootx(), win.winfo_pointerx()

    def press(event):
        length, tab, start, pointer = along()
        state["drag"] = (pointer, offset_for(length, tab, start), False)

    def motion(event):
        if not state["drag"]:
            return
        p0, off0, moved = state["drag"]
        length, tab, _, pointer = along()
        delta = pointer - p0
        if not moved and abs(delta) < 4:
            return
        state["drag"] = (p0, off0, True)
        preset = "top" if position() in VERTICAL else position()
        state["offset"] = offset_for(length, tab, tab_x(length, tab, preset, off0) + delta)
        draw()

    def release(event):
        drag, state["drag"] = state["drag"], None
        if not drag or not drag[2]:
            clicked(event)
            return
        state["offset_hold"] = time.time() + 5.0
        threading.Thread(target=save_setting, args=(state["options"].get("port"), {"notch_offset": state["offset"]}),
                         daemon=True).start()

    def toggle_pin(event):
        state["pinned"] = not state["pinned"]
        if state["pinned"]:
            hover(True)
        elif not pointer_inside():
            hover(False)

    label.bind("<ButtonPress-1>", press)
    label.bind("<B1-Motion>", motion)
    label.bind("<ButtonRelease-1>", release)
    label.bind("<Double-Button-1>", toggle_pin)

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
