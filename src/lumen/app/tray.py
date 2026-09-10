"""Running as a background app: tray icon, single instance, log redirection.

The tray icon is the brand mark with its two light surfaces painted from the
first two color devices (top = keyboard, bottom = light bar, exactly the
mapping in docs/brand/runtime), and offers: Open dashboard, Pause, Start at
login, Open log, Quit.
pystray needs the main thread (Cocoa insists), so the engine and HTTP server
run in daemon threads and the icon loop owns the process.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from lumen import paths
from lumen.core.engine import Engine

MAX_LOG_BYTES = 2 * 1024 * 1024
_mutex = None


def redirect_output_to_log(path: Path | None = None) -> bool:
    """A windowed build has no stdout at all (print() would raise); send it to
    the log. Truncate rather than rotate: it's a debugging tail."""
    if sys.stdout is not None and sys.stderr is not None:
        return False
    path = path or paths.log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        rotate_log(path)
    sys.stdout = sys.stderr = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    return True


def rotate_log(path: Path) -> None:
    """Keep one previous generation. Truncating in place used to throw away the
    very lines someone was about to read."""
    try:
        path.replace(path.with_name(path.name + ".1"))
    except OSError:
        path.write_text("", encoding="utf-8")


def claim_single_instance(name: str = "lumen-daemon") -> bool:
    """False if another daemon is already running (two would fight over the hardware)."""
    global _mutex
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        _mutex = kernel32.CreateMutexW(None, False, f"Local\\{name}")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    import fcntl
    lock = paths.data_dir() / f"{name}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    _mutex = open(lock, "w")
    try:
        fcntl.flock(_mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def open_dashboard(port: int) -> None:
    webbrowser.open(f"http://127.0.0.1:{port}/")


def open_log() -> None:
    log = paths.log_file()
    if not log.exists():
        return
    if sys.platform == "win32":
        os.startfile(log)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(log)])


BRAND_INK = (25, 28, 35)          # #191C23 — shell on a light system theme
BRAND_REVERSED = (255, 255, 255)  # #FFFFFF — shell on a dark one
BRAND_NEUTRAL = (163, 163, 173)   # #A3A3AD — a surface with no device behind it


@functools.lru_cache(maxsize=1)
def shell_color() -> tuple[int, int, int]:
    """Brand rule: dark shell on light backgrounds, white shell on dark ones.

    Cached — a theme switch is picked up on the next start, not live."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            with key:
                light, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return BRAND_INK if light else BRAND_REVERSED
        except OSError:
            return BRAND_REVERSED
    if sys.platform == "darwin":
        dark = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                              capture_output=True, text=True).stdout.strip() == "Dark"
        return BRAND_REVERSED if dark else BRAND_INK
    return BRAND_REVERSED


def tray_image(colors: list[tuple[int, int, int]], size: int = 64):
    """The brand capsule, its two surfaces filled with live device colors."""
    from PIL import Image, ImageDraw
    top = colors[0] if colors else BRAND_NEUTRAL
    bottom = colors[1] if len(colors) > 1 else top
    ss = 4  # supersample, then downscale: PIL has no antialiased drawing
    image = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Geometry from docs/brand/svg/lumen-icon.svg: a 168 x 294 capsule holding
    # surfaces at (30, 30, 108 x 115) and (30, 158, 108 x 106).
    width = size * 0.54
    height = width * 294 / 168
    scale = width / 168 * ss
    left, top_edge = (size * ss - width * ss) / 2, (size * ss - height * ss) / 2

    def box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return (left + x * scale, top_edge + y * scale,
                left + (x + w) * scale, top_edge + (y + h) * scale)

    draw.rounded_rectangle(box(0, 0, 168, 294), radius=84 * scale, fill=shell_color())
    draw.rounded_rectangle(box(30, 30, 108, 115), radius=42 * scale, fill=top)
    draw.rounded_rectangle(box(30, 158, 108, 106), radius=42 * scale, fill=bottom)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def run_tray(engine: Engine, port: int, stop: threading.Event, icon_ref: list | None = None) -> int:
    import pystray

    from lumen.app import autostart

    icon = pystray.Icon("lumen", tray_image([]), "Lumen")
    if icon_ref is not None:
        icon_ref.append(icon)

    def refresh():
        while not stop.is_set():
            colors = [c for d in engine.devices if (c := engine.player.current_color(d.id))]
            icon.icon = tray_image(colors)
            icon.title = "Lumen — paused" if engine.paused else f"Lumen — {len(engine.devices)} devices"
            time.sleep(1.0)

    def toggle_notch(item):
        new_val = not item.checked
        engine.config.set({"notch": new_val})
        engine.apply_settings()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda: open_dashboard(port), default=True),
        pystray.MenuItem("Paused", lambda _i, item: engine.set_paused(not item.checked), checked=lambda _: engine.paused),
        pystray.MenuItem("Notch status tab", lambda _i, item: toggle_notch(item),
                         checked=lambda _: bool(engine.config.settings.get("notch", True))),
        pystray.MenuItem("Reconnect devices", lambda: engine.scan()),
        pystray.MenuItem("Start at login", lambda _i, item: autostart.set_enabled(not item.checked),
                         checked=lambda _: autostart.enabled()),
        pystray.MenuItem("Open log", open_log),
        pystray.MenuItem("Quit", lambda: (stop.set(), icon.stop())),
    )
    threading.Thread(target=refresh, daemon=True).start()
    icon.run()
    stop.set()
    return 0


def run_app(no_tray: bool = False, open_ui: bool | None = None) -> int:
    """The daemon: engine + HTTP server (+ tray). Blocks until quit."""
    from lumen.server.api import serve_in_background

    windowed = redirect_output_to_log()
    if sys.platform == "win32":
        # Own taskbar identity, so Windows uses the app's own icon instead of
        # the host python.exe's and pins the app rather than the interpreter.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("latentspaces.lumen")
    if not claim_single_instance():
        print("lumen: another daemon is already running; exiting", flush=True)
        if windowed and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Lumen is already running — see the tray icon.", "Lumen", 0x40)
        return 1

    engine = Engine()
    port = int(engine.config.settings["port"])
    try:
        server = serve_in_background(engine, port)
    except OSError as e:
        print(f"lumen: cannot listen on 127.0.0.1:{port} ({e}); change `port` in settings", flush=True)
        return 1
    engine.start()
    print(f"lumen {engine.state()['version']}: dashboard at http://127.0.0.1:{port}/  ·  data in {paths.data_dir()}", flush=True)
    first_run = not engine.config.data["onboarded"]
    if open_ui or (open_ui is None and (first_run or not engine.config.settings["start_minimized"])):
        threading.Timer(0.5, open_dashboard, args=(port,)).start()

    stop = threading.Event()
    icon_ref: list = []

    def quit_app() -> None:      # the self-updater asks for this once the new binary is staged
        stop.set()
        if icon_ref:
            icon_ref[0].stop()

    engine.on_exit_request = quit_app
    try:
        if no_tray:
            while not stop.wait(1.0):
                pass
        else:
            try:
                import pystray  # noqa: F401
            except ImportError:
                print("lumen: pystray not installed; running without a tray icon", flush=True)
                while not stop.wait(1.0):
                    pass
            else:
                run_tray(engine, port, stop, icon_ref)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.shutdown()
    return 0
