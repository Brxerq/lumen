"""ASUS laptops with Aura Core lighting — ROG, TUF, Zephyrus — over raw HID:
keyboard zones plus the front light bar, no vendor software needed.

Protocol (report id 0x5D, 64-byte feature reports), as reverse engineered by
OpenRGB's AsusAuraCoreLaptopController and verified on a ROG Strix G513RM:

    5D BA C5 C4 <level>              brightness 0-3
    5D BC                            enter direct mode
    5D BC 00 01 04 00 00 00 00 + 18 RGB triplets
                                     triplets 0-3 keyboard zones (left to right)
                                     triplets 6-7 light bar halves
                                     4-5 and 8-17 hit nothing visible on the G513RM

Direct mode is volatile (Armoury Crate or the firmware can repaint), so the
effect player re-sends the base color every few seconds.

OpenRGB itself refuses laptops missing from its per-model DMI table, which is
why this adapter talks HID directly. Every Aura Core laptop exposes the same
interface (ASUS vendor id, usage page 0xFF31 / usage 0x79), so models other
than the one above are detected and flagged unverified in the device details
rather than ignored.
"""

from __future__ import annotations

import sys
import threading
from typing import cast

from lumen.core.devices import COLOR, RGB, ZONES, Device

VID = 0x0B05
USAGE_PAGE, USAGE = 0xFF31, 0x79
REPORT_LEN = 64
VERIFIED_PIDS = {0x19B6: "ROG Strix G513RM"}
KEYBOARD_ZONES, LIGHTBAR_ZONES = 4, 2


def report(*head: int, data: bytes = b"") -> bytes:
    body = bytes(head) + data
    return body + bytes(REPORT_LEN - len(body))


def brightness_report(level: int = 3) -> bytes:
    return report(0x5D, 0xBA, 0xC5, 0xC4, max(0, min(3, level)))


def init_direct_report() -> bytes:
    return report(0x5D, 0xBC)


def frame_report(keyboard: list[tuple[int, int, int]], lightbar: list[tuple[int, int, int]]) -> bytes:
    kb = (list(keyboard) + [keyboard[-1]] * KEYBOARD_ZONES)[:KEYBOARD_ZONES]
    lb = (list(lightbar) + [lightbar[-1]] * LIGHTBAR_ZONES)[:LIGHTBAR_ZONES]
    triplets = kb + [kb[-1]] * 2 + lb + [lb[-1]] * 10
    return report(0x5D, 0xBC, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x00, data=b"".join(bytes(t) for t in triplets))


class AuraController:
    """One HID handle, one frame, shared by the keyboard and light bar devices."""

    def __init__(self, path: bytes, pid: int):
        self.path, self.pid = path, pid
        self._dev = None
        self._lock = threading.Lock()
        self.keyboard = [(0, 0, 0)] * KEYBOARD_ZONES
        self.lightbar = [(0, 0, 0)] * LIGHTBAR_ZONES

    def _open(self):
        import hid
        dev = hid.device()
        dev.open_path(self.path)
        dev.send_feature_report(brightness_report())
        dev.send_feature_report(init_direct_report())
        return dev

    def flush(self) -> None:
        with self._lock:
            if self._dev is None:
                self._dev = self._open()
            try:
                self._dev.send_feature_report(frame_report(self.keyboard, self.lightbar))
            except (OSError, ValueError):
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except OSError:
                    pass
                self._dev = None


class AuraSurface(Device):
    def __init__(self, controller: AuraController, surface: str, zones: int, model: str, verified: bool):
        kind = "keyboard" if surface == "keyboard" else "lightbar"
        super().__init__(
            id=f"asus-aura-{surface}", name=f"ASUS {model} {'keyboard' if surface == 'keyboard' else 'light bar'}",
            kind=kind, vendor="ASUS", capabilities=frozenset({COLOR, ZONES}), zone_count=zones,
            refresh_every_s=5.0,
            details={"protocol": "Aura Core HID", "pid": f"0x{controller.pid:04X}",
                     "connection": "internal", **({} if verified else {"unverified": True})},
        )
        self._c, self._surface = controller, surface

    def set_zones(self, colors: list[RGB]) -> None:
        colors_out: list[RGB] = cast(list[RGB], [tuple(c) for c in colors][: self.zone_count])
        setattr(self._c, self._surface, colors_out)
        self._c.flush()

    def set_color(self, rgb: RGB) -> None:
        self.set_zones([cast(RGB, tuple(rgb))] * self.zone_count)

    def close(self) -> None:
        self._c.close()


# Vendor software owns the same direct-mode registers we do; when it is running
# both sides repaint and the user sees a fight rather than a bug.
RIVALS = {"win32": ["ArmouryCrate.exe", "ArmourySocketServer.exe", "LightingService.exe"]}


def vendor_software() -> str:
    """The name of a running vendor lighting app, or "" if none is."""
    try:
        import psutil
    except ImportError:
        return ""
    wanted = {name.lower(): name for name in RIVALS.get(sys.platform, [])}
    if not wanted:
        return ""
    try:
        for proc in psutil.process_iter(["name"]):
            if (name := (proc.info.get("name") or "").lower()) in wanted:
                return wanted[name]
    except Exception:
        return ""
    return ""


_cache: dict[bytes, list[Device]] = {}  # HID path -> the surfaces already handed out


def discover() -> list[Device]:
    if sys.platform not in ("win32", "linux"):
        return []
    try:
        import hid
    except ImportError:
        return []
    for d in hid.enumerate(VID, 0):
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            # The periodic rescan must hand back the *same* devices: a fresh
            # controller re-sends the brightness/direct-mode init and the old
            # handle gets closed, which the firmware answers with a visible
            # flicker every rescan interval.
            if d["path"] in _cache:
                for dev in _cache[d["path"]]:
                    dev.connected = True  # it enumerates, so a past write error is over
                return list(_cache[d["path"]])
            pid = d["product_id"]
            model = VERIFIED_PIDS.get(pid, d.get("product_string") or f"Aura laptop {pid:04X}")
            controller = AuraController(d["path"], pid)
            _cache.clear()
            surfaces = [
                AuraSurface(controller, "keyboard", KEYBOARD_ZONES, model, pid in VERIFIED_PIDS),
                AuraSurface(controller, "lightbar", LIGHTBAR_ZONES, model, pid in VERIFIED_PIDS),
            ]
            if (rival := vendor_software()):
                for surface in surfaces:
                    surface.details["hint"] = f"{rival} is running and repaints this device; close it if colours flicker."
            _cache[d["path"]] = cast(list[Device], surfaces)
            return list(surfaces)
    _cache.clear()  # unplugged/undetected: next detection gets a fresh handle
    return []
