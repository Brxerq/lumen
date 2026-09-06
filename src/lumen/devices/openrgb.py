"""Everything OpenRGB can drive: Razer, Corsair, Logitech, SteelSeries, MSI,
HyperX, motherboards, RAM, strips, fans... one adapter.

Lumen talks to the OpenRGB SDK server on 127.0.0.1:6742. If the server isn't
running but the app is installed, it launches it (setting `launch_openrgb`).
Every OpenRGB device becomes a Lumen device; keyboards with a key matrix get
column zones so `wave` sweeps across the keys.

Install OpenRGB from https://openrgb.org and enable its SDK server.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from lumen.core.devices import COLOR, ZONES, Device

HOST, PORT = "127.0.0.1", 6742
RETRY_AFTER_S = 10 * 60
_client = None
_retry_after = 0.0

_KINDS = {
    "KEYBOARD": "keyboard", "KEYPAD": "keyboard", "MOUSE": "mouse", "MOUSEMAT": "other", "HEADSET": "headset",
    "HEADSET_STAND": "headset", "LEDSTRIP": "strip", "LIGHT": "light", "SPEAKER": "other",
}


def _port_open(timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def executable() -> list[str] | None:
    """How to launch the OpenRGB server on this platform, or None if not installed."""
    if sys.platform == "darwin":
        if Path("/Applications/OpenRGB.app").exists():
            return ["open", "-a", "OpenRGB", "--args", "--server", "--noautoconnect"]
        return None
    for name in ("OpenRGB", "openrgb"):
        path = shutil.which(name)
        if path:
            return [path, "--server", "--noautoconnect"]
    if sys.platform == "win32":
        for base in ("C:/Program Files/OpenRGB", "C:/Program Files (x86)/OpenRGB"):
            exe = Path(base) / "OpenRGB.exe"
            if exe.exists():
                return [str(exe), "--server", "--noautoconnect"]
    return None


def launch_server(timeout: float = 15.0) -> bool:
    cmd = executable()
    if not cmd:
        return False
    flags = {"creationflags": 0x00000008} if sys.platform == "win32" else {}  # DETACHED_PROCESS
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open():
            time.sleep(1.0)  # let it finish enumerating
            return True
        time.sleep(0.5)
    return False


def _connect():
    global _client
    from openrgb import OpenRGBClient
    if _client is not None:
        try:
            _client.update()
            return _client
        except Exception:
            _client = None
    _client = OpenRGBClient(HOST, PORT, name="Lumen")
    return _client


class OpenRGBDevice(Device):
    def __init__(self, dev, index: int):
        name = dev.name.strip()
        type_name = getattr(dev.type, "name", str(dev.type))
        vendor = getattr(getattr(dev, "metadata", None), "vendor", "") or name.split(" ")[0]
        columns = _matrix_columns(dev)
        zones = len(columns) if columns else len(dev.zones)
        super().__init__(
            id=f"openrgb-{index}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}",
            name=name, kind=_KINDS.get(type_name, "other"), vendor=vendor,
            capabilities=frozenset({COLOR, ZONES} if zones > 1 else {COLOR}), zone_count=max(1, zones),
            details={"protocol": "OpenRGB SDK", "type": type_name.title(), "leds": len(dev.leds),
                     "connection": "OpenRGB"},
        )
        self._dev, self._columns = dev, columns
        try:
            dev.set_mode("Direct")
        except Exception:
            pass

    def set_color(self, rgb) -> None:
        from openrgb.utils import RGBColor
        self._dev.set_color(RGBColor(*rgb), fast=True)

    def set_zones(self, colors) -> None:
        from openrgb.utils import RGBColor
        if self._columns:
            frame = [RGBColor(*colors[0])] * len(self._dev.leds)
            for col, zone_color in zip(self._columns, colors):
                for led_index in col:
                    frame[led_index] = RGBColor(*zone_color)
            self._dev.set_colors(frame, fast=True)
            return
        for zone, zone_color in zip(self._dev.zones, colors):
            zone.set_color(RGBColor(*zone_color), fast=True)
        self._dev.show()


def _matrix_columns(dev) -> list[list[int]]:
    """LED indices per column, left to right, from the first zone's matrix map."""
    try:
        zone = dev.zones[0]
        mm = zone.matrix_map
        if not mm:
            return []
        cols = []
        for x in range(zone.mat_width):
            col = [mm[y][x] for y in range(zone.mat_height) if mm[y][x] is not None]
            if col:
                cols.append(col)
        return cols if len(cols) > 1 else []
    except (AttributeError, IndexError, TypeError):
        return []


def discover(settings: dict | None = None) -> list[Device]:
    settings = settings or {}
    try:
        import openrgb  # noqa: F401
    except ImportError:
        return []
    global _retry_after
    if time.monotonic() < _retry_after:
        return []
    if not _port_open():
        if not settings.get("launch_openrgb", True) or not launch_server():
            return []
    try:
        client = _connect()
    except Exception as e:
        # Something listens on 6742 but doesn't speak OpenRGB (or the server hung): don't
        # stall every rescan on it; try again in a while.
        _retry_after = time.monotonic() + RETRY_AFTER_S
        print(f"openrgb: connect failed: {type(e).__name__}: {e}; retrying in {RETRY_AFTER_S // 60} min", flush=True)
        return []
    return [OpenRGBDevice(d, i) for i, d in enumerate(client.devices) if d.leds]
