"""A colored glow along the screen edges — the "light bar" every machine has.

The glow is four thin borderless always-on-top windows drawn by tkinter in a
child process (Tk insists on owning a main thread, and on macOS *the* main
thread, which the tray icon already holds). The parent streams colors to the
child over stdin, one `r g b` line per update; a black color hides the glow.

    python -m lumen.devices.screen     # the child (source install)
    lumen screen-child                 # the child (frozen build; same code)

The child is started as soon as the device is discovered so the first effect
is not swallowed by process start-up, and the device object is a singleton so
periodic rescans don't respawn it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Any

from lumen.core.devices import COLOR, Device

THICKNESS_FRACTION = 0.012  # of the shorter screen edge
ALPHA = 0.85


def child_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "screen-child"]
    return [sys.executable, "-m", "lumen.devices.screen"]


class ScreenGlow(Device):
    def __init__(self):
        super().__init__(id="screen", name="Screen edge glow", kind="screen", vendor="",
                         capabilities=frozenset({COLOR}), details={"connection": "built-in"}, ambient=False)
        self._proc = None
        self._lock = threading.Lock()
        self.max_fps = 30

    def child_command(self) -> list[str]:
        return child_command()

    def _child(self):
        if self._proc is None or self._proc.poll() is not None:
            flags: dict[str, Any] = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            self._proc = subprocess.Popen(self.child_command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, text=True, **flags)
        return self._proc

    def warm_up(self) -> None:
        with self._lock:
            try:
                self._child()
            except OSError:
                self._proc = None

    def set_color(self, rgb) -> None:
        self._send("%d %d %d" % tuple(rgb))

    def _send(self, line: str) -> None:
        with self._lock:
            try:
                child = self._child()
                assert child.stdin is not None
                child.stdin.write(line + "\n")
                child.stdin.flush()
            except (OSError, ValueError) as e:
                self._proc = None
                raise OSError(f"screen glow child died: {e}") from e

    def close(self) -> None:
        # Rescans reuse this singleton; only the engine's shutdown/pause should kill the child.
        pass

    def shutdown(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.close()
                    self._proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    assert self._proc is not None
                    self._proc.kill()
            self._proc = None


_instance: ScreenGlow | None = None


def discover() -> list[Device]:
    global _instance
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return []
    if _instance is None:
        _instance = ScreenGlow()
    _instance.connected = True
    _instance.warm_up()
    return [_instance]


# ---------------------------------------------------------------------------
# Child process
# ---------------------------------------------------------------------------

def parse_line(line: str) -> tuple[int, int, int] | None:
    """One "r g b" line from the parent, or None for anything unparseable.

    The child must never die on a malformed line: the parent would take that as
    "the helper is gone" and respawn a window on top of the user's work."""
    try:
        r, g, b = (int(v) for v in line.split()[:3])
    except ValueError:
        return None
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def run_child() -> int:
    import queue
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    t = max(4, int(min(w, h) * THICKNESS_FRACTION))
    rects = [(0, 0, w, t), (0, h - t, w, t), (0, 0, t, h), (w - t, 0, t, h)]
    edges = []
    for x, y, ww, hh in rects:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            win.withdraw()
        win.geometry(f"{ww}x{hh}+{x}+{y}")
        win.configure(bg="#000000")
        edges.append(win)

    lines: queue.Queue = queue.Queue()

    def reader():
        for line in sys.stdin:
            lines.put(line.strip())
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def apply(rgb):
        hidden = rgb == (0, 0, 0)
        color = "#%02x%02x%02x" % rgb
        for win in edges:
            win.configure(bg=color)
            try:
                win.attributes("-alpha", 0.0 if hidden else ALPHA)
            except tk.TclError:
                if hidden:
                    win.withdraw()
                else:
                    win.deiconify()

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
        if last and (rgb := parse_line(last)) is not None:
            apply(rgb)
        root.after(16, poll)

    root.after(16, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_child())
