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
Tk as a PNG. Settings → "Status tab at the top of the screen" turns it off.

    python -m lumen.devices.notch     # the child (source install)
    lumen notch-child                 # the child (frozen build; same code)
"""

from __future__ import annotations

import json
import os
import sys

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

    def child_command(self) -> list[str]:
        return child_command()

    def set_color(self, rgb) -> None:
        self.set_zones([tuple(rgb)] * ZONE_COUNT)

    def set_zones(self, colors: list[RGB]) -> None:
        from lumen.integrations import claude_usage
        from lumen.integrations.agent_sessions import all_sessions
        payload = {"sessions": all_sessions(), "usage": claude_usage.latest()}
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
    _instance.connected = True
    _instance.warm_up()
    return [_instance]


# ---------------------------------------------------------------------------
# Child process: pure functions first (tested), then the Tk loop
# ---------------------------------------------------------------------------

def parse_line(line: str, n: int = ZONE_COUNT) -> tuple[list[RGB], list[dict], dict] | None:
    """One stdin line: zones, the session list and the usage summary (both empty if absent)."""
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
    return (zones, [s for s in sessions if isinstance(s, dict)] if isinstance(sessions, list) else [],
            {k: v for k, v in usage.items() if isinstance(v, dict) and isinstance(v.get("used"), (int, float))}
            if isinstance(usage, dict) else {})


def parse_zones(line: str, n: int = ZONE_COUNT) -> list[RGB] | None:
    """"r g b [r g b ...]" as exactly n zones (padded with the last colour), or None."""
    try:
        vals = [max(0, min(255, int(v))) for v in line.split()]
    except ValueError:
        return None
    if len(vals) < 3:
        return None
    zones = [tuple(vals[i:i + 3]) for i in range(0, len(vals) - len(vals) % 3, 3)]
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


def session_rows(sessions: list[dict]) -> list[tuple[str, str, str, int | None]]:
    """(agent, name, status, context %) per live session in zone order — the
    daemon's snapshot, so the rows match the bars and a tab's label is its name."""
    from lumen.integrations.agent_sessions import context_percent
    rows = sorted(sessions, key=lambda s: int(s.get("slot", 0)))
    return [(str(s.get("agent", "agent")),
             str(s.get("label") or os.path.basename(str(s.get("cwd") or "").rstrip("/\\")) or "~"),
             str(s.get("status")), context_percent(s)) for s in rows]


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


def render(zones: list[RGB], rows: list[tuple[str, str, str, int | None]], palette: dict[str, RGB],
           opaque_key: RGB | None = None, fills: list[int | None] | None = None, usage: dict | None = None,
           unfolded: bool = False, now: float | None = None):
    """The tab as an RGBA Pillow image (composited onto `opaque_key` where the
    platform can't do per-pixel alpha). `unfolded` = the hover panel: session
    rows, then the usage meters. `fills` is one context-window percentage per
    bar (None = solid bar); `usage` the 5-hour / 7-day summary, if known."""
    from PIL import Image, ImageDraw, ImageFilter

    from lumen.integrations.claude_usage import resets_in

    usage = usage or {}
    meters = [(name, usage[k]) for k, name in (("five_hour", "Session · 5 hours"), ("seven_day", "Week · 7 days"))
              if k in usage] if unfolded else []
    five = usage.get("five_hour", {}).get("used") if usage else None
    w = PANEL_WIDTH if unfolded else WIDTH
    h = HEIGHT
    if unfolded:
        h += PANEL_PAD + ROW * len(rows) + (PANEL_PAD // 2 + ROW * len(meters) if meters else 0) + PANEL_PAD - 6
    W, H, R = w * SS, h * SS, RADIUS * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # shell: flat top, rounded bottom — it hangs from the screen edge
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
                                 fill=colour + (150,))
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
        for agent, folder, status, pct in rows:
            colour = tuple(palette.get(status, MUTED[:3]))
            cy = y + ROW * SS // 2
            d.ellipse((BAR_INSET * SS, cy - 4 * SS, BAR_INSET * SS + 8 * SS, cy + 4 * SS), fill=colour + (255,))
            d.text((BAR_INSET * SS + 18 * SS, cy), agent.capitalize(), font=name_f, fill=INK + (255,), anchor="lm")
            nx = d.textlength(agent.capitalize(), font=name_f)
            d.text((BAR_INSET * SS + 18 * SS + nx + 10 * SS, cy), folder, font=meta_f, fill=MUTED + (255,), anchor="lm")
            label = LABELS.get(status, status)
            d.text((W - BAR_INSET * SS, cy), label, font=meta_f, fill=colour + (255,), anchor="rm")
            if pct is not None:  # context window, e.g. "63%", left of the state
                lx = W - BAR_INSET * SS - d.textlength(label, font=meta_f) - 12 * SS
                d.text((lx, cy), f"{pct}%", font=meta_f, fill=MUTED + (255,), anchor="rm")
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

    img = img.resize((w, h), Image.LANCZOS)
    if opaque_key is not None:
        back = Image.new("RGBA", img.size, opaque_key + (255,))
        back.alpha_composite(img)
        img = back
    return img


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
    sw = root.winfo_screenwidth()
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

    state = {"zones": [(0, 0, 0)] * ZONE_COUNT, "sessions": [], "usage": {}, "hover": False, "photo": None}

    def show(img):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        photo = tk.PhotoImage(data=buf.getvalue())
        state["photo"] = photo  # keep a reference or Tk drops the picture
        label.configure(image=photo)
        win.geometry(f"{img.width}x{img.height}+{(sw - img.width) // 2}+{top}")

    def draw():
        zones = state["zones"]
        if all(z == (0, 0, 0) for z in zones):
            try:
                win.attributes("-alpha", 0.0)
            except tk.TclError:
                win.withdraw()
            return
        rows = session_rows(state["sessions"])
        # one bar per tab (the effect merges same-colour neighbours): only then can a bar carry its tab's context
        fills = [r[3] for r in rows] if len(rows) == len(runs(zones)) else None
        show(render(zones, rows, DEFAULT_PALETTE, key, fills, state["usage"], unfolded=state["hover"]))
        try:
            win.attributes("-alpha", ALPHA)
        except tk.TclError:
            win.deiconify()

    def hover(on):
        if state["hover"] != on:
            state["hover"] = on
            draw()

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
        if last and (parsed := parse_line(last)) is not None and list(parsed) != [state["zones"], state["sessions"], state["usage"]]:
            state["zones"], state["sessions"], state["usage"] = parsed
            draw()
        root.after(16, poll)

    draw()
    root.after(16, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_child())
